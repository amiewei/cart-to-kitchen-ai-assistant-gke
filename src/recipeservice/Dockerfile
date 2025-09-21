# Use a multi-stage build to create a lean final image.
# STAGE 1: Builder
# In this stage, we install all build dependencies and Python packages.
FROM python:3.11 as builder

# Install build dependencies for packages like grpcio
RUN apt-get update && apt-get install -y build-essential

WORKDIR /app

# Copy Python dependencies
COPY src/recipeservice/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and proto files - this is meant to be called from the root directory
COPY src/recipeservice/. .
COPY protos ./protos

# Generate gRPC Python code from proto files
RUN python -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/recipe.proto ./protos/demo.proto


# STAGE 2: Final
# In this stage, we create the final lean image.
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy the application code and generated proto files from the builder stage
COPY --from=builder /app .

# Remove any existing generated images to keep container lean
RUN rm -rf generated_food_images || true

# Create empty directory for runtime image generation
RUN mkdir -p generated_food_images

# Set the command to run the gRPC server
CMD ["python", "grpc_server.py"]
