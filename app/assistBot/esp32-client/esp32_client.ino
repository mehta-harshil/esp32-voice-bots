// ## CODE
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>
#include <driver/i2s.h>
#include <SPIFFS.h>   // <-- CHANGED TO SPIFFS

// ------------------------------------------------------------------
// CONFIGURATION
// ------------------------------------------------------------------
const char* ssid = "Neurotric Labs_4G";
const char* password = "Neurotric@1327";
const char* server_ip = "192.168.1.12";
const uint16_t server_port = 8000;

#define TABLE_ID "table_4"

// Pin Definitions
#define PTT_BUTTON_PIN 13

// I2S Microphone (INMP441)
#define I2S_MIC_WS 25
#define I2S_MIC_SCK 26
#define I2S_MIC_SD 33

// I2S Speaker Amplifier (MAX98357A)
#define I2S_SPK_LRC 27
#define I2S_SPK_BCLK 14
#define I2S_SPK_DIN 12

// ------------------------------------------------------------------
// GLOBALS
// ------------------------------------------------------------------
WebSocketsClient webSocket;
bool isButtonPressed = false;
bool wasButtonPressed = false;

#define NUM_CHUNKS 28 // 112KB Total. Safe limit for ESP32 WROOM without crashing Wi-Fi/I2S
#define CHUNK_SIZE 4000 // 4KB per chunk
uint8_t* record_chunks[NUM_CHUNKS];
size_t total_record_length = 0;
int current_chunk = 0;
int current_offset = 0;

// ------------------------------------------------------------------
// I2S SETUP & MEMORY MANAGEMENT (DMA)
// ------------------------------------------------------------------
void setupI2S() {
  // 1. Microphone Setup (RX)
  i2s_config_t i2s_mic_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_I2S | I2S_COMM_FORMAT_I2S_MSB),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };
  
  i2s_pin_config_t i2s_mic_pins = {
    .mck_io_num = I2S_PIN_NO_CHANGE,
    .bck_io_num = I2S_MIC_SCK,
    .ws_io_num = I2S_MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_MIC_SD
  };

  i2s_driver_install(I2S_NUM_0, &i2s_mic_config, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &i2s_mic_pins);

  // 2. Speaker Setup (TX)
  i2s_config_t i2s_spk_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 16000, 
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_I2S | I2S_COMM_FORMAT_I2S_MSB),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };
  
  i2s_pin_config_t i2s_spk_pins = {
    .mck_io_num = I2S_PIN_NO_CHANGE,
    .bck_io_num = I2S_SPK_BCLK,
    .ws_io_num = I2S_SPK_LRC,
    .data_out_num = I2S_SPK_DIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };

  i2s_driver_install(I2S_NUM_1, &i2s_spk_config, 0, NULL);
  i2s_set_pin(I2S_NUM_1, &i2s_spk_pins);
}

// ------------------------------------------------------------------
// HTTP AUDIO DOWNLOAD -> SPIFFS -> I2S PLAYBACK
// ------------------------------------------------------------------
// ------------------------------------------------------------------
// HTTP AUDIO STREAMING (Direct to I2S)
// ------------------------------------------------------------------
void downloadAndPlayAudio() {
  HTTPClient http;
  String url = String("http://") + server_ip + ":" + server_port + "/audio/" + TABLE_ID;
  Serial.printf("[ESP32] 📡 Streaming audio from: %s\n", url.c_str());
  
  // Bug #3 fix: Add timeout tuning for unstable Wi-Fi
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.begin(url);
  int httpCode = http.GET();
  
  if (httpCode != 200) {
      Serial.printf("[ESP32] ❌ HTTP Error: %d\n", httpCode);
      http.end();
      return;
  }
  
  int totalBytes = http.getSize(); 
  if (totalBytes <= 44) {
      Serial.println("[ESP32] ❌ Audio too small, skipping.");
      http.end();
      return;
  }
  
  WiFiClient* stream = http.getStreamPtr();
  stream->setTimeout(5000); // ms, more reliable stall detection than millis() polling
  
  // ==========================================
  // REAL-TIME PLAYBACK PIPELINE
  // ==========================================
  Serial.println("[ESP32] ▶️ Streaming live to speaker...");

  // 1. Read and discard the 44-byte WAV header so it doesn't cause a "pop" sound
  uint8_t header[44];
  size_t headerBytesRead = stream->readBytes(header, 44);
  if (headerBytesRead < 44) {
      Serial.println("[ESP32] ⚠️ Stream timed out reading WAV header.");
      http.end();
      return;
  }

  // 2. Stream the rest directly to the I2S Amplifier
  uint8_t buffer[1024]; // Reverted to 1024 to prevent stack overflow! 4096 was too big for ESP32 stack.
  int bytesRemaining = totalBytes - 44;
  size_t bytes_written = 0;
  int totalPlayed = 0;
  
  while (bytesRemaining > 0 && stream->connected()) {
      int toRead = min((int)sizeof(buffer), bytesRemaining);
      int bytesRead = stream->readBytes(buffer, toRead);
      
      if (bytesRead > 0) {
          // Push directly to speaker!
          i2s_write(I2S_NUM_1, buffer, bytesRead, &bytes_written, portMAX_DELAY);
          bytesRemaining -= bytesRead;
          totalPlayed += bytesRead;
      } else {
          Serial.println("[ESP32] ⚠️ Stream timed out waiting for data.");
          break;
      }
      
      // Explicitly feed the Watchdog Timer so the ESP32 doesn't crash during long responses
      delay(1);
  }
  
  http.end();
  Serial.printf("[ESP32] 🏁 Streaming finished! Played %d PCM bytes live.\n", totalPlayed);
}

// ------------------------------------------------------------------
// WEBSOCKET HANDLER
// ------------------------------------------------------------------
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] Disconnected! Attempting to reconnect in background...");
      break;
    case WStype_CONNECTED:
      Serial.println("[WS] Connected to Central AI Server!");
      webSocket.sendTXT("{\"event\":\"connect\", \"table\":\"" TABLE_ID "\"}");
      break;
    case WStype_TEXT: {
      StaticJsonDocument<128> doc;
      DeserializationError err = deserializeJson(doc, payload, length);
      if (!err) {
          const char* event = doc["event"];
          if (event && strcmp(event, "audio_ready") == 0) {
              Serial.println("[WS] 🔔 Server says audio is ready! Starting HTTP download...");
              downloadAndPlayAudio();
          } else {
              Serial.printf("[WS] 💬 Received signal: %s\n", payload);
          }
      }
      break;
    }
    case WStype_BIN:
      Serial.printf("[WS] ⚠️ Unexpected binary frame: %u bytes (ignored)\n", length);
      break;
    case WStype_ERROR:
    case WStype_FRAGMENT_TEXT_START:
    case WStype_FRAGMENT_BIN_START:
    case WStype_FRAGMENT:
    case WStype_FRAGMENT_FIN:
      break;
  }
}

// ------------------------------------------------------------------
// INITIALIZATION
// ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  
  pinMode(PTT_BUTTON_PIN, INPUT_PULLUP);

  // 1. Connect Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");
  while(WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected successfully.");

  delay(2000);
  
  // Initialize SPIFFS
  // Passing 'true' formats the file system if it fails to mount (e.g., first time use)
  Serial.print("[ESP32] 💾 Initializing SPIFFS...");
  if (!SPIFFS.begin(true)) {
    Serial.println(" ❌ SPIFFS Mount Failed!");
  } else {
    Serial.println(" ✅ SPIFFS Ready!");
  }

  // 2. Setup I2S Audio Interfaces 
  setupI2S();

  // 3. Connect WebSockets
  webSocket.begin(server_ip, server_port, "/ws");
  webSocket.onEvent(webSocketEvent);
  
  webSocket.setReconnectInterval(5000); 

  // 4. Allocate audio memory chunks
  int allocated = 0;
  for (int i = 0; i < NUM_CHUNKS; i++) {
      record_chunks[i] = (uint8_t*)ps_malloc(CHUNK_SIZE);
      if (record_chunks[i] == NULL) {
          record_chunks[i] = (uint8_t*)malloc(CHUNK_SIZE);
      }
      if (record_chunks[i] != NULL) allocated++;
  }
  Serial.printf("[+] Successfully allocated %d/%d memory chunks (%d KB total)\n", allocated, NUM_CHUNKS, (allocated * CHUNK_SIZE) / 1024);

  // ------------------------------------------------------------------
  // 5. SPEAKER SELF-TEST
  // ------------------------------------------------------------------
  Serial.println("[TEST] 🔊 Playing speaker test beep (440Hz for 1 second)...");
  
  const int SAMPLE_RATE = 16000;
  const float FREQ = 440.0;           
  const int DURATION_MS = 1000;       
  const int TOTAL_SAMPLES = SAMPLE_RATE * DURATION_MS / 1000;
  const float AMPLITUDE = 10000.0;    
  
  int16_t beep_chunk[256];
  size_t bytes_written = 0;
  
  for (int sample = 0; sample < TOTAL_SAMPLES; sample += 256) {
      int chunk_samples = min(256, TOTAL_SAMPLES - sample);
      for (int i = 0; i < chunk_samples; i++) {
          float t = (float)(sample + i) / SAMPLE_RATE;
          beep_chunk[i] = (int16_t)(AMPLITUDE * sin(2.0 * PI * FREQ * t));
      }
      i2s_write(I2S_NUM_1, beep_chunk, chunk_samples * 2, &bytes_written, portMAX_DELAY);
  }
  
  memset(beep_chunk, 0, sizeof(beep_chunk));
  i2s_write(I2S_NUM_1, beep_chunk, sizeof(beep_chunk), &bytes_written, portMAX_DELAY);
  
  Serial.println("[TEST] ✅ Speaker test complete! If you heard a beep, speaker is working.");
}

// ------------------------------------------------------------------
// CORE LOGIC LOOP (STATE MACHINE)
// ------------------------------------------------------------------
void loop() {
  webSocket.loop();

  isButtonPressed = (digitalRead(PTT_BUTTON_PIN) == LOW);

  if (isButtonPressed && !wasButtonPressed) {
    Serial.println("[ESP32] 👇 Button Pressed! Recording started...");
    total_record_length = 0; 
    current_chunk = 0;
    current_offset = 0;
    
    i2s_zero_dma_buffer(I2S_NUM_1);
  }

  if (isButtonPressed) {
    size_t bytes_read;
    int32_t raw_samples[256]; 
    
    esp_err_t result = i2s_read(I2S_NUM_0, &raw_samples, sizeof(raw_samples), &bytes_read, portMAX_DELAY);
    
    if (result == ESP_OK && bytes_read > 0) {
      int samples_read = bytes_read / 4;
      int16_t audio_buffer_16[256];
      
      for (int i = 0; i < samples_read; i++) {
          audio_buffer_16[i] = raw_samples[i] >> 14; 
      }
      
      size_t bytes_to_copy = samples_read * 2;
      size_t bytes_copied = 0;
      
      while (bytes_copied < bytes_to_copy) {
          if (current_chunk >= NUM_CHUNKS || record_chunks[current_chunk] == NULL) {
              break;
          }
          
          size_t space_in_chunk = CHUNK_SIZE - current_offset;
          size_t chunk_copy_size = (bytes_to_copy - bytes_copied > space_in_chunk) ? space_in_chunk : (bytes_to_copy - bytes_copied);
          
          memcpy(record_chunks[current_chunk] + current_offset, (uint8_t*)audio_buffer_16 + bytes_copied, chunk_copy_size);
          
          current_offset += chunk_copy_size;
          bytes_copied += chunk_copy_size;
          total_record_length += chunk_copy_size;
          
          if (current_offset >= CHUNK_SIZE) {
              current_chunk++;
              current_offset = 0;
          }
      }
    }
  }

  if (!isButtonPressed && wasButtonPressed) {
    Serial.printf("[ESP32] 👆 Button released. Fast-uploading %d bytes of recorded audio...\n", total_record_length);
    
    if (total_record_length > 0) {
        int chunk_idx = 0;
        size_t sent_bytes = 0;
        
        while (sent_bytes < total_record_length) {
            if (record_chunks[chunk_idx] == NULL) break;
            
            size_t bytes_to_send = (total_record_length - sent_bytes > CHUNK_SIZE) ? CHUNK_SIZE : (total_record_length - sent_bytes);
            webSocket.sendBIN(record_chunks[chunk_idx], bytes_to_send);
            
            sent_bytes += bytes_to_send;
            chunk_idx++;
        }
    }
    
    Serial.println("[ESP32] ✅ Upload complete! Triggering AI Pipeline...");
    webSocket.sendTXT("{\"event\": \"stop_stream\"}");
  }

  wasButtonPressed = isButtonPressed;
}