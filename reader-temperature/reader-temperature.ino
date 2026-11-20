// reader-temperature.ino
// DHT11 → Blynk + Laurel Metrics API Ingestion
// Lee temperatura y humedad DHT11, envía a Blynk y a la API Laurel Metrics.
// Incluye conexión WiFi con timeout y diagnóstico.
// -------------------------------------------------------------------

#define BLYNK_PRINT Serial

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>

// === CONFIGURACIÓN ===
const char* ssid     = "LOPEZ";
const char* password = "Cacaocomegalletas";

// API Configuration (Laurel Metrics ingestion endpoint)
// HARD CODED per request: http://192.168.20.240:3021
const char* apiHost = "192.168.20.240";
const int apiPort = 3021;
const char* apiPath = "/api/ingest";

// SENSOR DHT
#define DHT_PIN   4
#define DHT_TYPE  DHT11

DHT dht(DHT_PIN, DHT_TYPE);

// Temperature/humidity state
float lastTemp = 0.0;
float lastHum = 0.0;
unsigned long lastApiPost = 0;
const long apiInterval = 8000;  // Enviar cada 8 segundos

// ==================================================================-
// Conexión WiFi con estado y timeout
// ==================================================================-
bool connectWiFi(unsigned long timeoutMs = 15000) {
  Serial.printf("\n📶 Conectando a \"%s\"...\n", ssid);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(100);
  WiFi.begin(ssid, password);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(500);
    Serial.print(".");
    if ((millis() - start) % 2000 < 500) {
      Serial.printf(" [estado=%d]", WiFi.status());
    }
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("✅ WiFi conectado");
    Serial.printf("📡 IP:      %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("🚪 Gateway: %s\n", WiFi.gatewayIP().toString().c_str());
    Serial.printf("📶 RSSI:    %d dBm\n", WiFi.RSSI());
    Serial.printf("🔐 BSSID:   %s\n", WiFi.BSSIDstr().c_str());
    Serial.printf("📻 Canal:   %d\n", WiFi.channel());
    return true;
  }

  Serial.printf("❌ Falló conexión. Código: %d\n", WiFi.status());
  switch (WiFi.status()) {
    case WL_NO_SSID_AVAIL:   Serial.println("   → SSID no encontrado (¿5GHz? ¿mayúsculas?)"); break;
    case WL_CONNECT_FAILED:  Serial.println("   → Contraseña incorrecta o WPA3"); break;
    case WL_CONNECTION_LOST: Serial.println("   → Conexión perdida"); break;
    case WL_DISCONNECTED:    Serial.println("   → Desconectado / timeout"); break;
  }

  Serial.println("🔍 Redes visibles:");
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    Serial.printf("   %2d) %-24s RSSI=%d canal=%d %s\n",
                  i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i),
                  WiFi.channel(i),
                  (WiFi.encryptionType(i) == WIFI_AUTH_OPEN) ? "abierta" : "cifrada");
  }
  WiFi.scanDelete();
  return false;
}

// ==================================================================-
// Lee el sensor DHT y envía datos a Blynk y API Laurel Metrics
// ==================================================================-
void enviarLecturaYApi() {
  float temp = dht.readTemperature();
  float hum = dht.readHumidity();

  if (isnan(temp) || isnan(hum)) {
    Serial.println("[DHT] Error: NaN — revisar cableado / pull-up");
    return;
  }

  lastTemp = temp;
  lastHum = hum;

  // Enviar a API Laurel Metrics (skill contract: one point per request)
  // Payload: {"name":"temperature","type":"gauge","value":<temp>,"unit":"C","humidity":<hum>,"humidity_unit":"%"}
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  if (millis() - lastApiPost < apiInterval) {
    return;
  }

  HTTPClient http;
  String url = String("http://") + apiHost + ":" + String(apiPort) + apiPath;

  // Payload: ONE point per request — skill contract
  String payload = "{\"name\":\"temperature\",\"type\":\"gauge\",\"value\":" + String(temp, 1) + ",\"unit\":\"C\",\"humidity\":" + String(hum, 1) + ",\"humidity_unit\":\"%\"}";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  int httpCode = http.POST(payload);

  // Logs precisos: temperatura, humedad y estado HTTP
  if (httpCode == 201) {
    Serial.printf("[API] Temp: %.1f, Hum: %.1f\n", temp, hum);
  } else if (httpCode == 409) {
    Serial.print("[API] Type mismatch heredado");
  } else if (httpCode >= 400) {
    Serial.print("[API] Error "); Serial.println(httpCode);
  } else {
    Serial.print("[API] OK   "); Serial.println(httpCode);
  }

  http.end();
  lastApiPost = millis();
}

// ==================================================================-
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("=================================");
  Serial.println("  DHT11 → Laurel Metrics API Ingest");
  Serial.println("=================================");

  dht.begin();
  delay(2000);

  Serial.print("Conectando a WiFi");
  connectWiFi();

  Serial.println("[SKILL] Contract: one point per request, type=gauge on first");
}

// ==================================================================-
void loop() {
  enviarLecturaYApi();

  delay(8000);  // Interval between sends
}