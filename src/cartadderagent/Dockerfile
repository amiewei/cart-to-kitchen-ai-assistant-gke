FROM python:3.11-slim
WORKDIR /app

# Install build dependencies for grpc-tools
RUN apt-get update && apt-get install -y build-essential

# Install requirements including grpc-tools for proto compilation
COPY src/cartadderagent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and proto files - this is meant to be called from the root directory
COPY src/cartadderagent/. .
COPY protos ./protos

# Generate gRPC Python code from proto files
RUN python -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/demo.proto

CMD ["python", "agent_server.py"]
