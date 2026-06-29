#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include <math.h>

// ------------------------------------------------------------------
// CONFIGURATION
// ------------------------------------------------------------------
const char* ssid = "Neurotric Labs_4G";
const char* password = "Neurotric@1327";
const char* server_ip = "192.168.1.12"; // Change to your Python server IP
const uint16_t server_port = 8000;

#define TABLE_ID "table_4"

// Pin Definitions
#define PTT_BUTTON_PIN 13

// RGB LED Pins
#define LED_R_PIN 14
#define LED_G_PIN 32 // <-- CHANGED FROM 12 TO 32 TO PREVENT BOOT CRASH
#define LED_B_PIN 27

// I2S Microphone (INMP441)
#define I2S_MIC_WS 25
#define I2S_MIC_SCK 26
#define I2S_MIC_SD 33

// ------------------------------------------------------------------
// GLOBALS & STATE MACHINE
// ------------------------------------------------------------------
WebSocketsClient webSocket;
bool isButtonPressed = false;
bool wasButtonPressed = false;

// Memory allocation for recording audio
#define NUM_CHUNKS 28 
#define CHUNK_SIZE 4000 
uint8_t* record_chunks[NUM_CHUNKS];
size_t total_record_length = 0;
int current_chunk = 0;
int current_offset = 0;

// Device States for Alexa-like LED
enum DeviceState { IDLE, RECORDING, PROCESSING };
DeviceState currentState = IDLE;
unsigned long processingStartTime = 0;

// ------------------------------------------------------------------
// RGB LED CONTROL (COMMON ANODE)
// ------------------------------------------------------------------
void setLEDColor(uint8_t r, uint8_t g, uint8_t b) {
  // Because it is a Common Anode (+) LED, the logic is inverted.
  // 255 turns the diode OFF, 0 turns it fully ON.
  analogWrite(LED_R_PIN, 255 - r);
  analogWrite(LED_G_PIN, 255 - g);
  analogWrite(LED_B_PIN, 255 - b);
}

void updateAlexaLEDs() {
  switch (currentState) {
    case IDLE:
      // Steady State: Very dim Blue
      setLEDColor(0, 0, 30); 
      break;

    case RECORDING:
      // Start Talking: Bright, solid Cyan
      setLEDColor(0, 255, 255); 
      break;

    case PROCESSING:
      // Processing: Smooth pulsing Magenta
      int pulse = (sin(millis() / 150.0) + 1) * 127; 
      setLEDColor(pulse, 0, pulse);

      // Return to IDLE automatically after 3.5 seconds
      if (millis() - processingStartTime > 3500) {
        currentState = IDLE;
      }
      break;
  }
}

// ------------------------------------------------------------------
// I2S SETUP (Microphone Only)
// ------------------------------------------------------------------
void setupI2S() {
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
}

// ------------------------------------------------------------------
// WEBSOCKET HANDLER
// ------------------------------------------------------------------
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] Disconnected!");
      break;
    case WStype_CONNECTED:
      Serial.println("[WS] Connected to Central AI Server!");
      webSocket.sendTXT("{\"event\":\"connect\", \"table\":\"" TABLE_ID "\"}");
      break;
    case WStype_TEXT: 
      Serial.printf("[WS] 💬 Server says: %s\n", payload);
      break;
    case WStype_BIN:
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

  // Setup RGB LED Pins
  pinMode(LED_R_PIN, OUTPUT);
  pinMode(LED_G_PIN, OUTPUT);
  pinMode(LED_B_PIN, OUTPUT);
  
  // Set initial state to IDLE
  currentState = IDLE;
  updateAlexaLEDs();

  // 1. Connect Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");
  while(WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n[+] WiFi connected successfully.");

  // 2. Setup I2S Microphone
  setupI2S();

  // 3. Connect WebSockets
  webSocket.begin(server_ip, server_port, "/ws");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000); 

  // 4. Allocate audio memory
  int allocated = 0;
  for (int i = 0; i < NUM_CHUNKS; i++) {
      record_chunks[i] = (uint8_t*)ps_malloc(CHUNK_SIZE);
      if (record_chunks[i] == NULL) {
          record_chunks[i] = (uint8_t*)malloc(CHUNK_SIZE);
      }
      if (record_chunks[i] != NULL) allocated++;
  }
  Serial.printf("[+] Allocated %d/%d memory chunks\n", allocated, NUM_CHUNKS);
  Serial.println("[+] System Ready! Hold the button to talk.");
}

// ------------------------------------------------------------------
// MAIN LOOP
// ------------------------------------------------------------------
void loop() {
  // Update LED animations smoothly
  updateAlexaLEDs();

  // Keep WebSocket alive
  webSocket.loop();

  // Check button state
  isButtonPressed = (digitalRead(PTT_BUTTON_PIN) == LOW);

  // --- STATE 1: BUTTON JUST PRESSED ---
  if (isButtonPressed && !wasButtonPressed) {
    Serial.println("[ESP32] 👇 Button Pressed! Recording started...");
    
    // Change LED State
    currentState = RECORDING;

    total_record_length = 0; 
    current_chunk = 0;
    current_offset = 0;
  }

  // --- STATE 2: BUTTON IS BEING HELD DOWN ---
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
          if (current_chunk >= NUM_CHUNKS || record_chunks[current_chunk] == NULL) break;
          
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

  // --- STATE 3: BUTTON JUST RELEASED ---
  if (!isButtonPressed && wasButtonPressed) {
    Serial.printf("[ESP32] 👆 Button released. Uploading %d bytes...\n", total_record_length);
    
    // Change LED State to Processing
    currentState = PROCESSING;
    processingStartTime = millis(); // Start the 3.5 second animation timer

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
    
    Serial.println("[ESP32] ✅ Upload complete!");
    webSocket.sendTXT("{\"event\": \"stop_stream\"}");
  }

  wasButtonPressed = isButtonPressed;
}