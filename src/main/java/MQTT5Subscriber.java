import org.eclipse.paho.mqttv5.client.MqttAsyncClient;
import org.eclipse.paho.mqttv5.client.MqttCallback;
import org.eclipse.paho.mqttv5.client.MqttConnectionOptions;
import org.eclipse.paho.mqttv5.client.MqttDisconnectResponse;
import org.eclipse.paho.mqttv5.client.IMqttToken;
import org.eclipse.paho.mqttv5.client.persist.MemoryPersistence;
import org.eclipse.paho.mqttv5.common.MqttException;
import org.eclipse.paho.mqttv5.common.MqttMessage;
import org.eclipse.paho.mqttv5.common.packet.MqttProperties;
import org.eclipse.paho.mqttv5.common.packet.UserProperty;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class MQTT5Subscriber {
    
    private static final String CLIENT_ID = "mqtt5-subscriber-" + System.currentTimeMillis();
    
    private MqttAsyncClient client;
    private volatile boolean running = true;
    private volatile boolean fullyConnected = false;
    private final CountDownLatch connectionLatch = new CountDownLatch(1);
    
    public static void main(String[] args) {
        // Print configuration first
        MqttConfig.printConfiguration();
        
        // Check if configuration is valid
        if (!MqttConfig.isConfigurationValid()) {
            System.err.println("Configuration is not valid. Please update MqttConfig.java");
            System.err.println("   Set USERNAME and PASSWORD to actual values");
            System.exit(1);
        }
        
        MQTT5Subscriber subscriber = new MQTT5Subscriber();
        try {
            subscriber.connect();
            subscriber.subscribe();
            subscriber.waitForMessages();
            subscriber.disconnect();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
    
    public void connect() throws MqttException, InterruptedException {
        System.out.println("Connecting to MQTT5 broker: " + MqttConfig.BROKER_URL);
        System.out.println("Using username: " + MqttConfig.USERNAME);
        System.out.println("Client ID: " + CLIENT_ID);
        
        // Create MQTT5 client
        client = new MqttAsyncClient(MqttConfig.BROKER_URL, CLIENT_ID, new MemoryPersistence());
        
        // Set up connection options for MQTT5
        MqttConnectionOptions options = new MqttConnectionOptions();
        options.setCleanStart(true); // Start with a clean session to avoid broker session conflicts
        options.setKeepAliveInterval(MqttConfig.KEEP_ALIVE_INTERVAL);
        options.setAutomaticReconnect(true);
        options.setConnectionTimeout(MqttConfig.CONNECTION_TIMEOUT);
        // Apply additional MQTT v5 options aligned with config
        options.setSessionExpiryInterval(MqttConfig.SESSION_EXPIRY_INTERVAL);
        options.setReceiveMaximum(MqttConfig.RECEIVE_MAXIMUM);
        options.setMaximumPacketSize(MqttConfig.MAX_PACKET_SIZE);
        
        // Set authentication credentials (if provided)
        if (MqttConfig.USERNAME != null && !MqttConfig.USERNAME.isEmpty()) {
            options.setUserName(MqttConfig.USERNAME);
            options.setPassword(MqttConfig.PASSWORD.getBytes());
        }
        
        // Set callback for handling messages and connection events
        client.setCallback(new MqttCallback() {
            @Override
            public void disconnected(MqttDisconnectResponse disconnectResponse) {
                System.out.println("Disconnected: " + disconnectResponse.getReasonString());
                fullyConnected = false;
            }
            
            @Override
            public void mqttErrorOccurred(MqttException exception) {
                System.err.println("MQTT Error: " + exception.getMessage());
            }
            
            @Override
            public void messageArrived(String topic, MqttMessage message) throws Exception {
                handleMessage(topic, message);
            }
            
            @Override
            public void deliveryComplete(IMqttToken token) {
                // Not used in subscriber
            }
            
            @Override
            public void connectComplete(boolean reconnect, String serverURI) {
                System.out.println("Connection completed to: " + serverURI + 
                                 (reconnect ? " (reconnected)" : " (initial connection)"));
                fullyConnected = true;
                connectionLatch.countDown(); // Signal that connection is truly complete
            }
            
            @Override
            public void authPacketArrived(int reasonCode, MqttProperties properties) {
                System.out.println("Auth packet received with reason code: " + reasonCode);
            }
        });
        
        // Connect and wait for BOTH token completion AND connectComplete callback
        try {
            System.out.println("Initiating connection...");
            IMqttToken token = client.connect(options);
            
            // Wait for the connect token to complete
            token.waitForCompletion(15000);
            System.out.println("Connection token completed");
            
            // Wait for the connectComplete callback to be triggered
            System.out.println("Waiting for connection to be fully established...");
            if (!connectionLatch.await(10, TimeUnit.SECONDS)) {
                System.err.println("Connection timeout - connectComplete callback not received");
                throw new MqttException(32000); // Use error code instead of string
            }
            
            // Double-check connection state
            if (client.isConnected() && fullyConnected) {
                System.out.println("Successfully connected to MQTT5 broker with authentication");
            } else {
                System.err.println("Connection failed - client state: connected=" + 
                    client.isConnected() + ", fullyConnected=" + fullyConnected);
                throw new MqttException(32001); // Use error code instead of string
            }
            
        } catch (MqttException e) {
            System.err.println("Failed to connect: " + e.getMessage());
            if (e.getMessage().contains("Not authorized") || e.getMessage().contains("authentication")) {
                System.err.println("   Authentication failed - check username/password in MqttConfig.java");
            }
            throw e;
        }
    }
    
    public void subscribe() throws MqttException, InterruptedException {
        System.out.println("Subscribing to topic: " + MqttConfig.TOPIC_BASE);
        
        // Wait a moment to ensure connection is stable
        Thread.sleep(500);
        
        // Verify connection before subscribing
        if (!client.isConnected() || !fullyConnected) {
            System.err.println("Cannot subscribe - client is not fully connected (connected=" + 
                client.isConnected() + ", fullyConnected=" + fullyConnected + ")");
            throw new MqttException(32104); // Client not connected error code
        }
        
        try {
            System.out.println("Sending subscription request...");
            IMqttToken token = client.subscribe(MqttConfig.TOPIC_BASE, MqttConfig.SUBSCRIBE_QOS);
            token.waitForCompletion(10000); // Wait up to 10 seconds
            System.out.println("Successfully subscribed to: " + MqttConfig.TOPIC_BASE);
        } catch (MqttException e) {
            System.err.println("Failed to subscribe: " + e.getMessage());
            System.err.println("   Connection state: connected=" + client.isConnected() + 
                             ", fullyConnected=" + fullyConnected);
            throw e;
        }
    }
    
    private void handleMessage(String topic, MqttMessage message) {
        System.out.println("\n=== Message Received ===");
        System.out.println("Topic: " + topic);
        System.out.println("QoS: " + message.getQos());
        System.out.println("Retained: " + message.isRetained());
        // Decode payload as UTF-8 explicitly to avoid platform default charset issues
        try {
            String payloadText = new String(message.getPayload(), java.nio.charset.StandardCharsets.UTF_8);
            System.out.println("Payload: " + payloadText);
        } catch (Exception decodeError) {
            System.out.println("Payload (raw bytes): " + new String(message.getPayload()));
        }
        
        // Extract metadata for logging
        MqttProperties properties = message.getProperties();
        java.util.List<UserProperty> userProps = (properties != null) ? properties.getUserProperties() : java.util.Collections.emptyList();
        String messageId = extractUserProperty(userProps, "messageId");
        String sensorId = extractSensorIdFromPayload(new String(message.getPayload(), java.nio.charset.StandardCharsets.UTF_8));
        
        if (MqttConfig.JSON_SERDES_ENABLED) {
            // SERDES deserialization with validation (requires schema registry)
            try {
                java.util.Map<String, Object> serdesHeaders = new java.util.HashMap<>();
                for (UserProperty prop : userProps) {
                    String key = prop.getKey();
                    if (key.equals("SCHEMA_ID_STRING") || key.startsWith("solace.schema.")) {
                        serdesHeaders.put(key, prop.getValue());
                    }
                }
                if (!serdesHeaders.containsKey("SCHEMA_ID_STRING")) {
                    serdesHeaders.put("SCHEMA_ID_STRING", MqttConfig.SCHEMA_ARTIFACT_ID);
                }
                com.solace.serdes.jsonschema.JsonSchemaDeserializer<JsonNode> deserializer =
                        SerdesSupport.getJsonDeserializer();
                JsonNode deserialized = deserializer.deserialize(topic, message.getPayload(), serdesHeaders);
                System.out.println("SERDES validation: PASSED");
                System.out.println("Deserialized JSON: " + SerdesSupport.jsonToString(deserialized));
                ValidationLogger.logSuccessfulValidation(
                        ValidationLogger.ClientType.SUBSCRIBER,
                        messageId != null ? messageId : "unknown",
                        MqttConfig.SCHEMA_ARTIFACT_ID,
                        topic,
                        CLIENT_ID,
                        sensorId);
            } catch (Exception e) {
                System.err.println("SERDES validation: FAILED");
                System.err.println("  Reason: " + e.getMessage());
                ValidationLogger.logSubscriberValidationFailure(
                        messageId != null ? messageId : "unknown",
                        MqttConfig.SCHEMA_ARTIFACT_ID,
                        topic,
                        e.getMessage(),
                        CLIENT_ID,
                        sensorId);
            }
        }
        
        // Handle MQTT5 message properties (reuse properties variable from above)
        if (properties != null) {
            System.out.println("\n--- MQTT5 Properties ---");
            
            if (properties.getMessageExpiryInterval() != null) {
                System.out.println("Message Expiry Interval: " + properties.getMessageExpiryInterval() + " seconds");
            }
            
            // Check if getPayloadFormat returns Boolean instead of boolean
            Boolean payloadFormat = properties.getPayloadFormat();
            if (payloadFormat != null) {
                System.out.println("Payload Format: " + (payloadFormat ? "UTF-8" : "Binary"));
            }
            
            if (properties.getContentType() != null) {
                System.out.println("Content Type: " + properties.getContentType());
            }
            
            if (properties.getResponseTopic() != null) {
                System.out.println("Response Topic: " + properties.getResponseTopic());
            }
            
            if (properties.getCorrelationData() != null) {
                System.out.println("Correlation Data: " + new String(properties.getCorrelationData(), java.nio.charset.StandardCharsets.UTF_8));
            }
            
            if (properties.getTopicAlias() != null) {
                System.out.println("Topic Alias: " + properties.getTopicAlias());
            }
            
            // Handle user properties
            if (!properties.getUserProperties().isEmpty()) {
                System.out.println("\n--- User Properties ---");
                for (UserProperty userProperty : properties.getUserProperties()) {
                    System.out.println("  - " + userProperty.getKey() + ": " + userProperty.getValue());
                }
                // Explicitly surface SERDES schema header if present
                properties.getUserProperties().stream()
                    .filter(up -> "SCHEMA_ID_STRING".equals(up.getKey()))
                    .findFirst()
                    .ifPresent(up -> System.out.println("SERDES Schema ID: " + up.getValue()));
            }
        }
        
        System.out.println("========================\n");
    }
    
    public void waitForMessages() throws InterruptedException {
        System.out.println("Waiting for messages... Press Ctrl+C to exit");
        System.out.println("   Broker: " + MqttConfig.BROKER_URL);
        System.out.println("   Topic: " + MqttConfig.TOPIC_BASE);
        
        // Add shutdown hook for graceful exit
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            System.out.println("\nShutdown signal received...");
            running = false;
        }));
        
        // Keep the subscriber running
        while (running && client.isConnected()) {
            Thread.sleep(1000);
        }
        
        if (!client.isConnected()) {
            System.err.println("Client disconnected unexpectedly");
        }
    }
    
    public void disconnect() throws MqttException, InterruptedException {
        System.out.println("Disconnecting from MQTT5 broker...");
        
        if (client != null && client.isConnected()) {
            try {
                // Unsubscribe first
                IMqttToken unsubToken = client.unsubscribe(MqttConfig.TOPIC_BASE);
                unsubToken.waitForCompletion(5000);
                System.out.println("Successfully unsubscribed from: " + MqttConfig.TOPIC_BASE);
                
                // Disconnect
                IMqttToken disconnectToken = client.disconnect();
                disconnectToken.waitForCompletion(5000);
                System.out.println("Successfully disconnected");
            } catch (MqttException e) {
                System.err.println("Disconnect failed: " + e.getMessage());
            } finally {
                try {
                    client.close();
                } catch (MqttException e) {
                    System.err.println("Failed to close client: " + e.getMessage());
                }
            }
        }
    }
    
    /**
     * Extract a user property value by key
     */
    private String extractUserProperty(java.util.List<UserProperty> userProps, String key) {
        return userProps.stream()
            .filter(up -> key.equals(up.getKey()))
            .map(UserProperty::getValue)
            .findFirst()
            .orElse(null);
    }
    
    /**
     * Extract sensor ID from JSON payload for correlation
     */
    private String extractSensorIdFromPayload(String payloadJson) {
        try {
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            com.fasterxml.jackson.databind.JsonNode node = mapper.readTree(payloadJson);
            if (node.has("sensorId")) {
                return node.get("sensorId").asText();
            }
        } catch (Exception e) {
            // Ignore parsing errors
        }
        return null;
    }
}
