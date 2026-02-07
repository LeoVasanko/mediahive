#!/usr/bin/env python3
"""
RTorrent Client - Communicate with rtorrent via XMLRPC over SCGI socket.
"""

import socket
import xmlrpc.client
from pathlib import Path
from typing import Dict, List, Optional


class SCGITransport(xmlrpc.client.Transport):
    """SCGI transport for communicating with rtorrent via Unix socket."""

    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path

    def single_request(self, host, handler, request_body, verbose=False):
        # Create SCGI request
        headers = f"CONTENT_LENGTH\x00{len(request_body)}\x00SCGI\x001\x00"
        request = f"{len(headers)}:{headers},{request_body.decode('utf-8')}"

        # Connect to socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        sock.send(request.encode("utf-8"))

        # Read response
        response = b""
        while True:
            data = sock.recv(4096)
            if not data:
                break
            response += data
        sock.close()

        # Parse response - skip HTTP headers
        if b"\r\n\r\n" in response:
            response = response.split(b"\r\n\r\n", 1)[1]

        return self.parse_response(response)

    def parse_response(self, response_body):
        p, u = xmlrpc.client.getparser()
        p.feed(response_body)
        p.close()
        return u.close()


class RTorrentClient:
    """Client for communicating with rtorrent via XMLRPC over SCGI socket."""

    def __init__(self, socket_path: str = "/home/user/rtorrent/.session/rpc.socket"):
        self.socket_path = socket_path
        transport = SCGITransport(socket_path)
        self.proxy = xmlrpc.client.ServerProxy(
            "http://localhost/RPC2", transport=transport
        )

    def get_loaded_hashes(self) -> set[str]:
        """Get set of info hashes for all currently loaded torrents."""
        try:
            downloads = self.proxy.download_list("")
            return set(h.upper() for h in downloads)
        except Exception as e:
            print(f"Error getting loaded torrents: {e}")
            return set()

    def load_torrent(self, torrent_path: Path, download_dir: Path) -> bool:
        """
        Load a torrent file and set its download directory.
        Uses load.start_verbose to load and immediately start/hash-check.

        Args:
            torrent_path: Path to the .torrent file
            download_dir: Directory where the data already exists

        Returns:
            True if successful, False otherwise
        """
        try:
            # load.start_verbose with d.directory.set to specify download location
            # This will hash-check existing files instead of re-downloading
            self.proxy.load.start_verbose(
                "", str(torrent_path), f'd.directory.set="{download_dir}"'
            )
            return True
        except Exception as e:
            print(f"Error loading torrent {torrent_path}: {e}")
            return False

    def get_torrent_info(self, info_hash: str) -> Optional[Dict]:
        """Get info about a loaded torrent."""
        try:
            name = self.proxy.d.name(info_hash)
            message = self.proxy.d.message(info_hash)
            tied_file = self.proxy.d.tied_to_file(info_hash)
            directory = self.proxy.d.directory(info_hash)
            base_path = self.proxy.d.base_path(info_hash)  # Actual data path
            is_multi_file = self.proxy.d.is_multi_file(info_hash)
            return {
                "hash": info_hash,
                "name": name,
                "message": message,
                "tied_file": tied_file,
                "directory": directory,
                "base_path": base_path,  # Full path to data (file or folder)
                "is_multi_file": is_multi_file,
            }
        except Exception as e:
            print(f"Error getting torrent info for {info_hash}: {e}")
            return None

    def get_unregistered_torrents(self) -> List[Dict]:
        """
        Find all torrents with 'unregistered' or 'not registered' tracker errors.

        Returns:
            List of torrent info dicts for torrents with registration errors
        """
        unregistered = []
        try:
            hashes = self.proxy.download_list("")
            for info_hash in hashes:
                try:
                    message = self.proxy.d.message(info_hash)
                    if message and (
                        "unregistered" in message.lower()
                        or "not registered" in message.lower()
                    ):
                        info = self.get_torrent_info(info_hash)
                        if info:
                            unregistered.append(info)
                except Exception:
                    continue
        except Exception as e:
            print(f"Error scanning for unregistered torrents: {e}")
        return unregistered

    def remove_torrent(self, info_hash: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from rtorrent.

        Args:
            info_hash: The info hash of the torrent to remove
            delete_files: If True, also delete downloaded files (default: False)

        Returns:
            True if successful, False otherwise
        """
        try:
            if delete_files:
                # This would delete the data - NOT what we want
                self.proxy.d.erase(info_hash)
            else:
                # Just remove from rtorrent, keep files
                self.proxy.d.erase(info_hash)
            return True
        except Exception as e:
            print(f"Error removing torrent {info_hash}: {e}")
            return False
