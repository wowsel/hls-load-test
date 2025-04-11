/**
 * Copyright [2025] [wowsel]
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import re

from locust import FastHttpUser, task, between, events
import m3u8
import random
import gevent
from gevent.lock import BoundedSemaphore
from gevent.queue import Queue
from urllib.parse import urlparse, parse_qs, urlencode, urljoin
import logging
if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
    from gevent import monkey
    monkey.patch_all()
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


# Add custom command-line arguments
@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument("--master-url", type=str,
                        default='https://ewr1.vultrobjects.com/moments/streams/bunny/master.m3u8',
                        env_var="MASTER_URL",
                        help="URL of the master playlist(s) in format \"url\",\"url\".")
    parser.add_argument("--vod-buffer-duration", type=int,
                        default=40,
                        env_var="VOD_BUFFER_DURATION",
                        help="Buffer size for VOD in seconds.")
    parser.add_argument("--vod-switch-interval", type=int,
                        default=300,
                        env_var="VOD_SWITCH_INTERVAL",
                        help="Interval between random position changes in VOD, in seconds. Set to 0 to disable random switching.")
    parser.add_argument("--filter-host-segments", 
                        action="store_true",
                        default=True,
                        env_var="FILTER_HOST_SEGMENTS",
                        help="Only download segments that start with the host URL. Set to False to download all segments.")
    parser.add_argument("--download-full-segments", 
                        action="store_true",
                        default=False,
                        env_var="DOWNLOAD_FULL_SEGMENTS",
                        help="Download full segment content instead of just the first few bytes.")

class HLSUser(FastHttpUser):
    """
    User class that simulates an HLS player client.
    Uses FastHttpUser for better performance during load testing.
    """
    wait_time = between(1, 3)  # Time between tasks
    connection_timeout = 5     # Connection timeout in seconds
    network_timeout = 5        # Network timeout in seconds
    
    def __init__(self, *args, **kwargs):
        # Set default host if not provided
        self.host = self.host or "https://moments.example.com"
        super().__init__(*args, **kwargs)
        try:
            # Parse master URL(s) and choose one randomly if multiple are provided
            urls_list = [url.strip() for url in self.environment.parsed_options.master_url.replace('"', '').split(',')]
            self.master_url = random.choice(urls_list)
        except (ValueError, IndexError):
            self.master_url = "WRONG_URL"

    def on_start(self):
        """
        Initialize the user session when it starts.
        Sets up all necessary variables and starts the HLS playback.
        """
        # Initialize user session variables
        self.sessionid = None              # Session ID for stateful connections
        self.selected_variant = None       # Selected HLS variant
        self.playlist_uri = None           # URI of the selected variant playlist
        self.playback_position = 0         # Current playback position in seconds
        self.segment_queue = Queue()       # Queue for segments to download
        self.semaphore = BoundedSemaphore(1)  # Semaphore for thread synchronization
        self.buffer_duration = self.environment.parsed_options.vod_buffer_duration or 40  # Fixed buffer size for VOD
        self.buffered_duration = 0         # Current buffer level in seconds
        self.last_downloaded_sequence = None  # Last downloaded segment sequence number
        self.stream_type = None            # Stream type: 'LIVE' or 'VOD'
        self.switch_interval = self.environment.parsed_options.vod_switch_interval or 300  # Interval for random position switch
        self.playlist_segments = []        # List of segments for VOD
        self.total_segments = 0            # Total number of segments in VOD
        self.current_segment_index = 0     # Current segment index for VOD
        # Initialize the filtering settings
        self.filter_host_segments = self.environment.parsed_options.filter_host_segments
        self.download_full_segments = self.environment.parsed_options.download_full_segments

        self.running = True                # Flag indicating if user is active
        self.greenlets = []                # List of running greenlets (background tasks)

        logging.debug("Starting HLS playback")
        self.start_hls_playback()

    def on_stop(self):
        """
        Clean up when the user session ends.
        Sets running flag to false and kills all background greenlets.
        """
        # Set stop flag
        self.running = False
        # Stop all background greenlets
        for greenlet in self.greenlets:
            greenlet.kill()

    def fix_master_quotes(self, master_text):
        """
        Fix quotes in master playlist to ensure compatibility with m3u8 library.
        Some servers may provide incorrectly quoted attributes.
        
        Args:
            master_text: The text content of the master playlist
            
        Returns:
            Fixed playlist text
        """
        pattern = r'PROGRAM-ID="(\d+)"'
        return re.sub(pattern, r'PROGRAM-ID=\1', master_text)

    def start_hls_playback(self):
        """
        Start HLS playback by fetching the master playlist, selecting a variant,
        and initiating the necessary background tasks.
        """
        logging.debug("Inside start_hls_playback")
        try:
            master_url = self.master_url
            headers = {}

            # Request master.m3u8
            response = self.client.get(master_url, allow_redirects=False,
                                       name=f"GET master-redirect.m3u8")

            # Process the response
            if response.status_code == 302:
                # Handle redirect (typically for session initialization)
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    parsed_url = urlparse(redirect_url)
                    query_params = parse_qs(parsed_url.query)
                    self.sessionid = query_params.get('sessionid', [None])[0]

                    # Request master playlist with sessionid
                    master_response = self.client.get(redirect_url, headers=headers,
                                                      name=f"GET master-session.m3u8")
                    master_playlist = m3u8.loads(self.fix_master_quotes(master_response.text))
                else:
                    logging.error("Location header not found in 302 response.")
                    return
            elif response.status_code == 200:
                # Direct response with the master playlist
                master_playlist = m3u8.loads(self.fix_master_quotes(response.text))
                logging.debug("Server returned master playlist without redirection.")
            else:
                logging.error(f"Unexpected status code {response.status_code}")
                return

            # Check if the playlist contains variants (master playlist) or segments (media playlist)
            if master_playlist.playlists:
                logging.debug("Master playlist with variants detected.")
                # Logic to select a variant playlist
                variants_with_resolution = []

                # Find variants with 720p resolution
                for playlist in master_playlist.playlists:
                    stream_info = playlist.stream_info
                    resolution = stream_info.resolution
                    if resolution == (1280, 720):
                        variants_with_resolution.append(playlist)

                # Select the variant with the highest bandwidth among 720p variants,
                # or a random variant if no 720p is available
                if variants_with_resolution:
                    self.selected_variant = max(
                        variants_with_resolution,
                        key=lambda p: int(p.stream_info.bandwidth or 0)
                    )
                else:
                    if master_playlist.playlists:
                        self.selected_variant = random.choice(master_playlist.playlists)
                    else:
                        logging.error("Could not find variants in master playlist.")
                        return

                # Save playlist URI
                self.playlist_uri = self.selected_variant.uri

                # Add sessionid to playlist_uri if needed
                if self.sessionid and 'sessionid' not in self.playlist_uri:
                    parsed_uri = urlparse(self.playlist_uri)
                    query_params = parse_qs(parsed_uri.query)
                    query_params['sessionid'] = self.sessionid
                    new_query = urlencode(query_params, doseq=True)
                    self.playlist_uri = parsed_uri._replace(query=new_query).geturl()

                # Convert relative path to full URL
                if not self.playlist_uri.startswith('http'):
                    self.playlist_uri = urljoin(self.host, self.playlist_uri)

                logging.debug(f"Selected playlist: {self.playlist_uri}")

                # Request the selected variant playlist
                playlist_response = self.client.get(self.playlist_uri,
                                                    name=f"GET stream.m3u8")
                if playlist_response.status_code != 200:
                    logging.error(f"Error getting playlist: status code {playlist_response.status_code}")
                    return

                variant_playlist = m3u8.loads(playlist_response.text)
            elif master_playlist.segments:
                logging.debug("Media playlist with segments detected.")
                # If the playlist already contains segments, treat it as variant_playlist
                variant_playlist = master_playlist
                self.playlist_uri = master_url  # Playlist already loaded
            else:
                logging.error("Playlist contains neither variants nor segments.")
                return

            # Determine stream type
            if variant_playlist.is_endlist or variant_playlist.playlist_type == 'vod':
                self.stream_type = 'VOD'
                logging.debug("VOD stream detected")
            else:
                self.stream_type = 'LIVE'
                logging.debug("LIVE stream detected")

            # Start background tasks
            self.greenlets.append(gevent.spawn(self.update_playback_position))
            self.greenlets.append(gevent.spawn(self.download_segments))

            if self.stream_type == 'LIVE':
                self.greenlets.append(gevent.spawn(self.update_playlist))
            elif self.stream_type == 'VOD':
                # Save segments for VOD
                self.playlist_segments = variant_playlist.segments
                self.total_segments = len(self.playlist_segments)
                # Start random segment switching loop (if enabled)
                if self.switch_interval > 0:
                    self.greenlets.append(gevent.spawn(self.switch_random_segment_loop))
                # Initialize playback from first segment
                self.current_segment_index = 0
                self.buffered_duration = 0
                self.segment_queue = Queue()
                self.last_downloaded_sequence = self.current_segment_index - 1
                # Fill buffer for VOD starting from the first segment
                self.add_segments_to_queue_for_vod()

        except Exception as e:
            logging.error(f"Error in start_hls_playback: {e}")

    @task
    def hls_task(self):
        """Empty task for Locust. The actual work is done in background tasks."""
        gevent.sleep(1)

    def switch_random_segment_loop(self):
        """
        Periodically switch to a random segment for VOD streams.
        Simulates a user jumping to different parts of the video.
        If switch_interval is 0, this function will exit immediately (random switching disabled).
        """
        try:
            # If switch_interval is 0, disable random switching
            if self.switch_interval <= 0:
                logging.info("Random segment switching is disabled (switch_interval is 0)")
                return
                
            while self.running:
                gevent.sleep(self.switch_interval)
                if self.running:
                    self.switch_random_segment()
        except Exception as e:
            logging.error(f"Error in switch_random_segment_loop: {e}")

    def switch_random_segment(self):
        """Switch to a random segment for VOD streams with thread safety."""
        if not self.running:
            return
        with self.semaphore:
            self.switch_to_random_segment()

    def switch_to_random_segment(self):
        """
        Choose a random segment and initialize the buffer.
        Used to simulate users jumping to different parts of a VOD stream.
        """
        self.current_segment_index = random.randint(0, self.total_segments - 1)
        self.buffered_duration = 0
        self.segment_queue = Queue()
        self.last_downloaded_sequence = self.current_segment_index - 1
        logging.debug(f"Switching to random segment with index {self.current_segment_index}")
        # Fill buffer for VOD
        self.add_segments_to_queue_for_vod()

    def add_segments_to_queue_for_vod(self):
        """
        Add new segments to the queue for VOD to maintain the buffer.
        Will add segments until the buffer duration is reached or all segments are used.
        """
        index = self.last_downloaded_sequence + 1
        accumulated_duration = self.buffered_duration

        while accumulated_duration < self.buffer_duration and index < self.total_segments:
            ts_segment = self.playlist_segments[index]
            ts_uri = ts_segment.uri
            duration = ts_segment.duration

            # Add segment to queue
            self.segment_queue.put((ts_uri, duration, index))
            accumulated_duration += duration
            index += 1

        # Update last added segment
        self.last_added_sequence = index - 1 if index > 0 else self.current_segment_index

    def update_playback_position(self):
        """
        Update the playback position by incrementing it every second.
        Simulates actual video playback by the client.
        """
        logging.debug("Started update_playback_position function")
        try:
            while self.running:
                gevent.sleep(1)
                with self.semaphore:
                    self.playback_position += 1
                    # Decrease buffer level by 1 second
                    self.buffered_duration = max(0, self.buffered_duration - 1)
                    logging.debug(f"Playback position: {self.playback_position} sec, Buffer: {self.buffered_duration} sec")

                    # For VOD, check if we've reached the end of the playlist
                    if self.stream_type == 'VOD' and self.last_downloaded_sequence >= self.total_segments - 1 and self.buffered_duration == 0:
                        logging.debug("End of VOD playlist reached, switching to random segment")
                        self.switch_to_random_segment()
        except Exception as e:
            logging.error(f"Error in update_playback_position: {e}")

    def update_playlist(self):
        """
        Periodically update the playlist for LIVE streams.
        This is necessary to get new segments as they become available.
        Only applicable for LIVE streams.
        """
        if self.stream_type != 'LIVE':
            return  # Only for LIVE streams

        logging.debug("Started update_playlist function")
        try:
            while self.running:
                gevent.sleep(5)  # Check for updates every 5 seconds
                playlist_response = self.client.get(
                    self.playlist_uri,
                    name=f"GET stream.m3u8"
                )
                if playlist_response.status_code != 200:
                    logging.error(f"Error getting playlist: status code {playlist_response.status_code}")
                    continue

                variant_playlist = m3u8.loads(playlist_response.text)

                with self.semaphore:
                    # Dynamically calculate buffer duration for LIVE streams
                    if self.buffer_duration == 0:
                        total_playlist_duration = sum(segment.duration for segment in variant_playlist.segments)
                        self.buffer_duration = total_playlist_duration / 2
                        logging.info(f"Calculated buffer set to {self.buffer_duration} sec")

                    media_sequence = variant_playlist.media_sequence

                    if self.last_downloaded_sequence is None:
                        self.last_downloaded_sequence = media_sequence - 1

                    # Add new segments to the queue
                    for index, ts_segment in enumerate(variant_playlist.segments):
                        segment_sequence = media_sequence + index
                        ts_uri = ts_segment.uri
                        duration = ts_segment.duration

                        if segment_sequence > self.last_downloaded_sequence:
                            # Skip if segment already in queue
                            if ts_uri in [item[0] for item in self.segment_queue.queue]:
                                continue

                            self.segment_queue.put((ts_uri, duration, segment_sequence))
                        else:
                            continue
        except Exception as e:
            logging.error(f"Error in update_playlist: {e}")

    def download_segments(self):
        """
        Download segments from the queue to maintain the buffer.
        Works for both LIVE and VOD streams.
        """
        logging.debug("Started download_segments function")
        try:
            while self.running:
                gevent.sleep(1)
                with self.semaphore:
                    # Check if buffer needs more segments
                    if self.buffered_duration < self.buffer_duration:
                        # For VOD, add new segments to the queue if it's empty
                        if self.stream_type == 'VOD' and self.segment_queue.empty():
                            self.add_segments_to_queue_for_vod()

                        if not self.segment_queue.empty():
                            ts_uri, duration, segment_sequence = self.segment_queue.get()

                            # Add sessionid to TS URI if needed
                            if self.sessionid and 'sessionid' not in ts_uri:
                                parsed_ts_uri = urlparse(ts_uri)
                                query_params = parse_qs(parsed_ts_uri.query)
                                query_params['sessionid'] = self.sessionid
                                new_query = urlencode(query_params, doseq=True)
                                ts_uri = parsed_ts_uri._replace(query=new_query).geturl()

                            # Convert relative path to full URL
                            ts_url_full = urljoin(self.playlist_uri, ts_uri)

                            # Check URL based on filter setting and download TS segment
                            should_download = True
                            if self.filter_host_segments and not ts_url_full.startswith(self.host):
                                should_download = False
                                logging.debug(f"Skipping segment with URL not matching host: {ts_url_full}")

                            if should_download:
                                logging.debug(f"Downloading TS segment: {ts_url_full}")

                                # Prepare headers based on download mode
                                headers = {}
                                if not self.download_full_segments:
                                    headers = {'Range': 'bytes=0-0'}
                                    
                                # Download segment
                                ts_response = self.client.get(
                                    ts_url_full,
                                    headers=headers,
                                    name=f"GET TS Segment"
                                )

                                # Increase buffer level by segment duration
                                self.buffered_duration += duration
                                # Update last downloaded segment
                                self.last_downloaded_sequence = segment_sequence

                                if ts_response.status_code in [200, 206]:
                                    logging.debug(f"TS segment downloaded successfully: {ts_url_full}")
                                else:
                                    logging.error(f"Error downloading TS segment: status code {ts_response.status_code}")
                            else:
                                # Still update buffer and sequence even if we didn't download
                                self.buffered_duration += duration
                                self.last_downloaded_sequence = segment_sequence
                        else:
                            # Queue is empty, wait before next check
                            gevent.sleep(1)
                    else:
                        # Buffer is full, wait before next check
                        gevent.sleep(1)
        except Exception as e:
            logging.error(f"Error in download_segments: {e}")
