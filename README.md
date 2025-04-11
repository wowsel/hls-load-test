# HLS Load Testing Script with Locust

This script is designed for load testing HLS (HTTP Live Streaming) streams using Locust. It automatically detects whether a stream is LIVE or VOD (Video On Demand) and adjusts its behavior accordingly to simulate realistic viewer behavior.

## Features

- **Automatic Stream Type Detection**: Automatically detects whether the stream is LIVE or VOD based on playlist tags.
- **Dynamic Buffer Management**:
  - For LIVE streams, the buffer size is dynamically calculated based on the playlist duration.
  - For VOD streams, the buffer size is fixed at a configurable default of 40 seconds.
- **Random Playback Position**: For VOD streams, the script can select a random initial segment and switches to a new random segment at configurable intervals.
- **Efficient Processing**: Uses `FastHttpUser` for improved performance under heavy load.
- **Timeout Handling**: Sets a configurable timeout for HTTP requests to prevent hanging.
- **Path Flexibility**: Supports both absolute URLs and relative paths in the `master-url` parameter.
- **Minimal Bandwidth Usage**: By default, the script only downloads segment headers (first few bytes) to verify availability without streaming the actual content.
- **Flexible Segment Filtering**: Can be configured to download all segments or only segments from the specified host.
- **Multiple Configuration Options**: Allows setting `host`, `master-url`, buffer sizes, and switching intervals via command-line arguments or environment variables.
- **Docker and Kubernetes Support**: The script can be run inside a Docker container or deployed to Kubernetes.

## Requirements

- Python 3.7+
- Locust
- `m3u8` library
- Other dependencies listed in requirements.txt

## Installation

Clone the repository or copy the script to your local machine.

Install the required Python packages:

```bash
pip install -r requirements.txt
```

## Usage

### Command-line Arguments

- `--host`: Base URL of the HLS stream (e.g., `https://example.com`).
- `--master-url`: URL or path to the master playlist. Can be:
  - Absolute URL (e.g., `https://example.com/playlist.m3u8`)
  - Relative path (e.g., `/api/livestreaming?url=https://example.com/playlist.m3u8`)
- `--vod-buffer-duration`: Buffer size for VOD streams in seconds (default: 40).
- `--vod-switch-interval`: Interval between random position changes in VOD streams, in seconds (default: 300). Set to 0 to disable random switching and play VOD linearly from beginning to end.
- `--filter-host-segments`: When set to `True` (default), only download segments with URLs starting with the host. Set to `False` to download all segments regardless of their URL.
- `--download-full-segments`: When set to `True`, download the entire segment content. Default is `False`, which only downloads the first few bytes of each segment to verify availability.

### Running the Script

You can run the script directly with Locust:

```bash
# Using relative path
locust --host=https://example.com --master-url=/api/livestreaming?url=https://example.com/playlist.m3u8
```

```bash
# Using full URL
locust --host=https://example.com --master-url=https://cdn.example.com/streams/playlist.m3u8
```

To download all segments (not just those starting with the host URL):

```bash
# Using relative path
locust --host=https://example.com --master-url=/api/livestreaming?url=https://example.com/playlist.m3u8 --filter-host-segments=False
```

```bash
# Using full URL
locust --host=https://example.com --master-url=https://cdn.example.com/streams/playlist.m3u8 --filter-host-segments=False
```

To download full segment content instead of just headers:

```bash
# Using relative path
locust --host=https://example.com --master-url=/api/livestreaming?url=https://example.com/playlist.m3u8 --download-full-segments=True
```

```bash
# Using full URL
locust --host=https://example.com --master-url=https://cdn.example.com/streams/playlist.m3u8 --download-full-segments=True
```

For multiple master URLs (to test different streams), separate them with commas:

```bash
# Using relative paths
locust --host=https://example.com --master-url="/api/stream1,/api/stream2"
```

```bash
# Using full URLs
locust --host=https://example.com --master-url="https://cdn1.example.com/stream1.m3u8,https://cdn2.example.com/stream2.m3u8"
```

```bash
# Using a mix of relative paths and full URLs
locust --host=https://example.com --master-url="/api/stream1,https://cdn.example.com/stream2.m3u8"
```

### Docker Usage

#### Building the Docker Image

Build the Docker image:

```bash
docker build -t locust-hls .
```

#### Running the Docker Container

Run the container with the required parameters:

```bash
docker run -p 8089:8089 locust-hls --host=https://example.com --master-url=/api/livestreaming?url=https://example.com/playlist.m3u8
```

You can then access the Locust web interface at `http://localhost:8089`.

### Kubernetes Deployment

The repository includes Helm charts for deployment to Kubernetes. These charts are provided as examples and starting points that should be customized for your specific environment.

#### Helm Chart Structure

- `.helm/values.yaml`: Main configuration file for the load test deployment
- `.helm/templates/`: Contains the Kubernetes manifest templates

#### Deployment

To deploy using the Helm chart:

```bash
helm upgrade --install hls-load-test .helm/ --namespace hls-test
```

#### Customizing the Helm Chart

The included Helm chart is provided as an example only. You should customize the following in `.helm/values.yaml`:

- **Ingress Configuration**: Update hostnames, TLS settings, and annotations
- **Load Test Parameters**: Modify environment variables like `LOCUST_HOST`, `MASTER_URL`, etc.
- **Resource Allocations**: Adjust CPU and memory requests/limits
- **Node Selectors and Tolerations**: Configure where pods should be scheduled
- **Replica Count**: Change the number of worker pods for larger tests
- **Storage**: Add persistent volumes if needed for test results

Example customization:

```yaml
# In .helm/values.yaml
ingresses:
  master:
    hosts:
      - hostname: locust.your-domain.com  # Change to your domain

deployments:
  master:
    containers:
    - name: locust
      env:
        - name: LOCUST_HOST
          value: "https://your-streaming-server.com"  # Change to your target server
        - name: LOCUST_USERS
          value: "1000"  # Change number of simulated users
```

For more advanced Kubernetes deployments, refer to the [Locust documentation on distributed load testing](https://docs.locust.io/en/stable/running-distributed.html).

## Script Overview

The script simulates HLS clients that:

1. Fetch the master playlist.
2. Choose a variant playlist based on resolution preference (preferring 720p) and bandwidth.
3. Determine if the stream is LIVE or VOD based on playlist tags.
4. Process segments without actually downloading or playing the media content.

### For LIVE streams:
- Continuously update the playlist and download new segments as they become available.
- Dynamically calculate and maintain an appropriate buffer size.

### For VOD streams:
- Download the playlist once.
- Start playback from a random segment.
- Switch to a new random segment periodically (if enabled).
- Maintain a fixed buffer size, downloading segments as needed.

### Resource Efficiency

The script uses the following optimizations to reduce resource usage:
- Only downloads a few bytes of each segment using Range requests to verify availability without consuming bandwidth (can be changed with `--download-full-segments=True`)
- By default, only processes segments that match the specified host URL (can be changed with `--filter-host-segments=False`)
- Efficiently manages buffer levels without actually streaming the content

## Key Components

### `HLSUser` Class
- Inherits from `FastHttpUser` for efficient HTTP requests.
- Implements the main logic for handling HLS streams and buffer management.

### Buffer Management
- Uses a queue to manage the list of segments to download.
  - For LIVE streams, the buffer size is dynamically calculated as half the total playlist duration.
  - For VOD streams, the buffer size is fixed at a configurable value (default: 40 seconds).

### Concurrency and Synchronization
- Uses `gevent` for asynchronous operations and green threads.
- Implements `BoundedSemaphore` to synchronize access to shared resources.

### Error Handling and Logging
- Sets timeouts for all HTTP requests to prevent hanging.
- Logs important events and errors with various logging levels.

### Main Functions

- **`start_hls_playback()`**: Initiates HLS playback by fetching the master playlist and selecting a variant.
- **`update_playlist()`**: For LIVE streams, periodically requests the variant playlist to get new segments.
- **`download_segments()`**: Downloads segments from the queue to maintain the buffer at the desired level.
- **`update_playback_position()`**: Simulates playback by incrementing the position counter and reducing buffer level.
- **`switch_random_segment()`**: For VOD streams, switches to a new random segment periodically.
- **`fix_master_quotes()`**: Fixes formatting in the master playlist for compatibility with the `m3u8` library.

## Configuration

The script is highly configurable through command-line arguments and environment variables:

- **Host**: `--host` or the default in the script (https://moments.example.com).
- **Master URL**: `--master-url` or the `MASTER_URL` environment variable.
- **Buffer Size**: `--vod-buffer-duration` or the `VOD_BUFFER_DURATION` environment variable.
- **Switch Interval**: `--vod-switch-interval` or the `VOD_SWITCH_INTERVAL` environment variable.
  - Set to `0` to disable random segment switching and play the VOD linearly from the beginning.

## Tips for Effective Load Testing

1. **Start Small**: Begin with a small number of users to verify correct behavior.
2. **Monitor Server Metrics**: Track server CPU, memory, network, and disk I/O.
3. **Gradual Scaling**: Increase the number of users gradually to identify bottlenecks.
4. **Tune Parameters**: Adjust buffer sizes and switching intervals based on the specific streaming scenario.
5. **Use Multiple Streams**: Test with different streams to ensure comprehensive coverage.
6. **Testing Modes**: For VOD testing, consider both:
   - Linear playback (set `vod-switch-interval=0`) to simulate users watching from start to finish.
   - Random seek behavior (default) to simulate users jumping around the video.

## Troubleshooting

- **Connection Issues**: Verify the host URL and master URL are accessible from the test environment.
- **Memory Problems**: If running with many users, increase the available memory for the Locust process.
- **Timeouts**: Adjust the connection and network timeouts if necessary for your environment.

## Resources

- [Locust Documentation](https://docs.locust.io/en/stable/): Official documentation for the Locust load testing framework
- [HLS Specification](https://datatracker.ietf.org/doc/html/rfc8216): The RFC for HTTP Live Streaming
- [m3u8 Library Documentation](https://github.com/globocom/m3u8): Python library for parsing M3U8 files


