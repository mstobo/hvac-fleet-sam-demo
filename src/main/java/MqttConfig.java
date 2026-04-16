/**
 * Configuration utility class for MQTT5 connection settings and credentials.
 * Modify the constants in this class to match your broker configuration.
 */
public class MqttConfig {
    
    // Broker Configuration
    public static final String BROKER_URL = "ssl://mr-connection-yfx6c4y9zy1.messaging.solace.cloud:8883";
    // For TLS/SSL connections, use: "ssl://your-broker:8883"
    // For WebSocket connections, use: "ws://your-broker:8080/mqtt"
    // For WebSocket Secure connections, use: "wss://your-broker:8443/mqtt"
    
    // Authentication Configuration
    public static final String USERNAME = "solace-cloud-client";     // Change this to your username
    public static final String PASSWORD = "b0a6gq4es61hosgu4ovghosgte";     // Change this to your password
    
    // Topic Configuration
    public static final String TOPIC_BASE = "test/mqtt5/messages";
    public static final String RESPONSE_TOPIC = "test/mqtt5/responses";
    
    // Client Configuration
    public static final int CONNECTION_TIMEOUT = 10;          // seconds
    public static final int KEEP_ALIVE_INTERVAL = 60;         // seconds
    public static final long SESSION_EXPIRY_INTERVAL = 3600L; // 1 hour in seconds
    public static final int RECEIVE_MAXIMUM = 100;            // max in-flight messages
    public static final long MAX_PACKET_SIZE = 1024 * 1024L;  // 1MB
    
    // Message Configuration
    public static final int DEFAULT_QOS = 1;                  // 0, 1, or 2
    // Parameterized QoS values for subscriber and publisher
    public static final int SUBSCRIBE_QOS = 0;                // Recommended for this service
    public static final int PUBLISH_QOS = 0;                  // Keep consistent with subscriber
    public static final long MESSAGE_EXPIRY_INTERVAL = 300L;  // 5 minutes in seconds
    
    // Schema Registry Configuration (fill in credentials)
    public static final String SCHEMA_REGISTRY_URL = "https://apis.18.221.21.40.nip.io/apis/registry/v3";
    public static final String SCHEMA_REGISTRY_USERNAME = "sr-developer";
    public static final String SCHEMA_REGISTRY_PASSWORD = "admin";
    public static final String SCHEMA_ARTIFACT_ID = "solace/samples/tempsensor";
    // Use validation for JSON Schema during serialization/deserialization
    // ENABLED - Schema Registry validation active
    public static final boolean JSON_VALIDATE_SCHEMA = true;
    public static final boolean JSON_SERDES_ENABLED = true;
    public static final boolean JSON_PUBLISH_WITH_SERDES = true;
    
    // Common broker configurations for reference:
    
    // Solace PubSub+ Cloud
    // BROKER_URL = "ssl://your-messaging-service.messaging.solace.cloud:8883"
    // USERNAME = "solace-cloud-client"
    // PASSWORD = "your-password"
    
    // Mosquitto with authentication
    // BROKER_URL = "tcp://localhost:1883"
    // USERNAME = "mqtt-user"
    // PASSWORD = "mqtt-password"
    
    // AWS IoT Core
    // BROKER_URL = "ssl://your-endpoint.iot.region.amazonaws.com:8883"
    // Uses certificate-based authentication instead of username/password
    
    // HiveMQ Cloud
    // BROKER_URL = "ssl://your-cluster.s1.eu.hivemq.cloud:8883"
    // USERNAME = "your-username"
    // PASSWORD = "your-password"
    
    /**
     * Generates a unique client ID with the given prefix
     */
    public static String generateClientId(String prefix) {
        return prefix + "-" + System.currentTimeMillis();
    }
    
    /**
     * Validates that required configuration values are set
     */
    public static boolean isConfigurationValid() {
        return !USERNAME.equals("your-username") && 
               !PASSWORD.equals("your-password") &&
               !BROKER_URL.isEmpty();
    }
    
    /**
     * Prints current configuration (excluding password for security)
     */
    public static void printConfiguration() {
        System.out.println("=== MQTT5 Configuration ===");
        System.out.println("Broker URL: " + BROKER_URL);
        System.out.println("Username: " + USERNAME);
        System.out.println("Password: " + (PASSWORD.equals("your-password") ? "[NOT SET]" : "[CONFIGURED]"));
        System.out.println("Topic Base: " + TOPIC_BASE);
        System.out.println("Connection Timeout: " + CONNECTION_TIMEOUT + "s");
        System.out.println("Keep Alive: " + KEEP_ALIVE_INTERVAL + "s");
        System.out.println("Session Expiry: " + SESSION_EXPIRY_INTERVAL + "s");
        System.out.println("Default QoS: " + DEFAULT_QOS);
        System.out.println("========================\n");
        
        if (!isConfigurationValid()) {
            System.err.println("WARNING: Please update the configuration in MqttConfig.java");
            System.err.println("   Set USERNAME, PASSWORD, and BROKER_URL to match your broker");
        }
    }
}
