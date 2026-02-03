FROM grafana/alloy:latest

# Updated to use OTLP HTTP exporter for Grafana Cloud traces (fixed auth header)
COPY config.alloy /etc/alloy/config.alloy

CMD ["run", "/etc/alloy/config.alloy"]
