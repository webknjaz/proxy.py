# -*- coding: utf-8 -*-
"""
    proxy.py
    ~~~~~~~~
    ⚡⚡⚡ Fast, Lightweight, Pluggable, TLS interception capable proxy server focused on
    Network monitoring, controls & Application development, testing, debugging.

    :copyright: (c) 2013-present by Abhinav Singh and contributors.
    :license: BSD, see LICENSE for more details.
"""
import gzip
import os
import selectors
import tempfile

import unittest
from unittest import mock

from proxy.common.constants import CRLF, PLUGIN_HTTP_PROXY, PLUGIN_PAC_FILE, PLUGIN_WEB_SERVER, PROXY_PY_DIR
from proxy.common.flag import FlagParser
from proxy.common.plugins import Plugins
from proxy.common.utils import build_http_request, build_http_response, bytes_, text_
from proxy.core.connection import TcpClientConnection
from proxy.http import HttpProtocolHandler
from proxy.http.parser import HttpParser, httpParserStates, httpParserTypes
from proxy.http.server import HttpWebServerPlugin


class TestWebServerPlugin(unittest.TestCase):

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def setUp(self, mock_fromfd: mock.Mock, mock_selector: mock.Mock) -> None:
        self.fileno = 10
        self._addr = ('127.0.0.1', 54382)
        self._conn = mock_fromfd.return_value
        self.mock_selector = mock_selector
        self.flags = FlagParser.initialize(threaded=True)
        self.flags.plugins = Plugins.load([
            bytes_(PLUGIN_HTTP_PROXY),
            bytes_(PLUGIN_WEB_SERVER),
        ])
        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=self.flags,
        )
        self.protocol_handler.initialize()

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def test_pac_file_served_from_disk(
            self, mock_fromfd: mock.Mock, mock_selector: mock.Mock,
    ) -> None:
        pac_file = os.path.join(
            os.path.dirname(PROXY_PY_DIR),
            'helper',
            'proxy.pac',
        )
        self._conn = mock_fromfd.return_value
        self.mock_selector_for_client_read(mock_selector)
        self.init_and_make_pac_file_request(pac_file)
        self.protocol_handler._run_once()
        self.assertEqual(
            self.protocol_handler.request.state,
            httpParserStates.COMPLETE,
        )
        with open(pac_file, 'rb') as f:
            self._conn.send.called_once_with(
                build_http_response(
                    200, reason=b'OK', headers={
                        b'Content-Type': b'application/x-ns-proxy-autoconfig',
                        b'Connection': b'close',
                    }, body=f.read(),
                ),
            )

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def test_pac_file_served_from_buffer(
            self, mock_fromfd: mock.Mock, mock_selector: mock.Mock,
    ) -> None:
        self._conn = mock_fromfd.return_value
        self.mock_selector_for_client_read(mock_selector)
        pac_file_content = b'function FindProxyForURL(url, host) { return "PROXY localhost:8899; DIRECT"; }'
        self.init_and_make_pac_file_request(text_(pac_file_content))
        self.protocol_handler._run_once()
        self.assertEqual(
            self.protocol_handler.request.state,
            httpParserStates.COMPLETE,
        )
        self._conn.send.called_once_with(
            build_http_response(
                200, reason=b'OK', headers={
                    b'Content-Type': b'application/x-ns-proxy-autoconfig',
                    b'Connection': b'close',
                }, body=pac_file_content,
            ),
        )

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def test_default_web_server_returns_404(
            self, mock_fromfd: mock.Mock, mock_selector: mock.Mock,
    ) -> None:
        self._conn = mock_fromfd.return_value
        mock_selector.return_value.select.return_value = [
            (
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_READ,
                    data=None,
                ),
                selectors.EVENT_READ,
            ),
        ]
        flags = FlagParser.initialize(threaded=True)
        flags.plugins = Plugins.load([
            bytes_(PLUGIN_HTTP_PROXY),
            bytes_(PLUGIN_WEB_SERVER),
        ])
        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=flags,
        )
        self.protocol_handler.initialize()
        self._conn.recv.return_value = CRLF.join([
            b'GET /hello HTTP/1.1',
            CRLF,
        ])
        self.protocol_handler._run_once()
        self.assertEqual(
            self.protocol_handler.request.state,
            httpParserStates.COMPLETE,
        )
        self.assertEqual(
            self.protocol_handler.work.buffer[0],
            HttpWebServerPlugin.DEFAULT_404_RESPONSE,
        )

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def test_static_web_server_serves(
            self, mock_fromfd: mock.Mock, mock_selector: mock.Mock,
    ) -> None:
        # Setup a static directory
        static_server_dir = os.path.join(tempfile.gettempdir(), 'static')
        index_file_path = os.path.join(static_server_dir, 'index.html')
        html_file_content = b'''<html><head></head><body><h1>Proxy.py Testing</h1></body></html>'''
        os.makedirs(static_server_dir, exist_ok=True)
        with open(index_file_path, 'wb') as f:
            f.write(html_file_content)

        self._conn = mock_fromfd.return_value
        self._conn.recv.return_value = build_http_request(
            b'GET', b'/index.html',
        )

        mock_selector.return_value.select.side_effect = [
            [(
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_READ,
                    data=None,
                ),
                selectors.EVENT_READ,
            )],
            [(
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_WRITE,
                    data=None,
                ),
                selectors.EVENT_WRITE,
            )],
        ]

        flags = FlagParser.initialize(
            enable_static_server=True,
            static_server_dir=static_server_dir,
            threaded=True,
        )
        flags.plugins = Plugins.load([
            bytes_(PLUGIN_HTTP_PROXY),
            bytes_(PLUGIN_WEB_SERVER),
        ])

        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=flags,
        )
        self.protocol_handler.initialize()

        self.protocol_handler._run_once()
        self.protocol_handler._run_once()

        self.assertEqual(mock_selector.return_value.select.call_count, 2)
        self.assertEqual(self._conn.send.call_count, 1)
        encoded_html_file_content = gzip.compress(html_file_content)

        # parse response and verify
        response = HttpParser(httpParserTypes.RESPONSE_PARSER)
        response.parse(self._conn.send.call_args[0][0])
        self.assertEqual(response.code, b'200')
        self.assertEqual(response.header(b'content-type'), b'text/html')
        self.assertEqual(response.header(b'cache-control'), b'max-age=86400')
        self.assertEqual(response.header(b'content-encoding'), b'gzip')
        self.assertEqual(response.header(b'connection'), b'close')
        self.assertEqual(
            response.header(b'content-length'),
            bytes_(len(encoded_html_file_content)),
        )
        assert response.body
        self.assertEqual(gzip.decompress(response.body), html_file_content)

    @mock.patch('selectors.DefaultSelector')
    @mock.patch('socket.fromfd')
    def test_static_web_server_serves_404(
            self,
            mock_fromfd: mock.Mock,
            mock_selector: mock.Mock,
    ) -> None:
        self._conn = mock_fromfd.return_value
        self._conn.recv.return_value = build_http_request(
            b'GET', b'/not-found.html',
        )

        mock_selector.return_value.select.side_effect = [
            [(
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_READ,
                    data=None,
                ),
                selectors.EVENT_READ,
            )],
            [(
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_WRITE,
                    data=None,
                ),
                selectors.EVENT_WRITE,
            )],
        ]

        flags = FlagParser.initialize(enable_static_server=True, threaded=True)
        flags.plugins = Plugins.load([
            bytes_(PLUGIN_HTTP_PROXY),
            bytes_(PLUGIN_WEB_SERVER),
        ])

        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=flags,
        )
        self.protocol_handler.initialize()

        self.protocol_handler._run_once()
        self.protocol_handler._run_once()

        self.assertEqual(mock_selector.return_value.select.call_count, 2)
        self.assertEqual(self._conn.send.call_count, 1)
        self.assertEqual(
            self._conn.send.call_args[0][0],
            HttpWebServerPlugin.DEFAULT_404_RESPONSE,
        )

    @mock.patch('socket.fromfd')
    def test_on_client_connection_called_on_teardown(
            self, mock_fromfd: mock.Mock,
    ) -> None:
        flags = FlagParser.initialize(threaded=True)
        plugin = mock.MagicMock()
        flags.plugins = {b'HttpProtocolHandlerPlugin': [plugin]}
        self._conn = mock_fromfd.return_value
        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=flags,
        )
        self.protocol_handler.initialize()
        plugin.assert_called()
        with mock.patch.object(self.protocol_handler, '_run_once') as mock_run_once:
            mock_run_once.return_value = True
            self.protocol_handler.run()
        self.assertTrue(self._conn.closed)
        plugin.return_value.on_client_connection_close.assert_called()

    def init_and_make_pac_file_request(self, pac_file: str) -> None:
        flags = FlagParser.initialize(pac_file=pac_file, threaded=True)
        flags.plugins = Plugins.load([
            bytes_(PLUGIN_HTTP_PROXY),
            bytes_(PLUGIN_WEB_SERVER),
            bytes_(PLUGIN_PAC_FILE),
        ])
        self.protocol_handler = HttpProtocolHandler(
            TcpClientConnection(self._conn, self._addr),
            flags=flags,
        )
        self.protocol_handler.initialize()
        self._conn.recv.return_value = CRLF.join([
            b'GET / HTTP/1.1',
            CRLF,
        ])

    def mock_selector_for_client_read(self, mock_selector: mock.Mock) -> None:
        mock_selector.return_value.select.return_value = [
            (
                selectors.SelectorKey(
                    fileobj=self._conn,
                    fd=self._conn.fileno,
                    events=selectors.EVENT_READ,
                    data=None,
                ),
                selectors.EVENT_READ,
            ),
        ]
