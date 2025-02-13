#  Copyright (c) Lightstreamer Srl.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#  Modifications copyright (c) 2018 Wojciech Szlachta

import logging
import threading
import traceback
from urllib.parse import (urlparse as parse_url, urljoin, urlencode)
from urllib.request import urlopen as _urlopen

CONNECTION_URL_PATH = "lightstreamer/create_session.txt"
BIND_URL_PATH = "lightstreamer/bind_session.txt"
CONTROL_URL_PATH = "lightstreamer/control.txt"
# Request parameter to create and activate a new Table.
OP_ADD = "add"
# Request parameter to delete a previously created Table.
OP_DELETE = "delete"
# Request parameter to force closure of an existing session.
OP_DESTROY = "destroy"
# List of possible server responses
PROBE_CMD = "PROBE"
END_CMD = "END"
LOOP_CMD = "LOOP"
ERROR_CMD = "ERROR"
SYNC_ERROR_CMD = "SYNC ERROR"
OK_CMD = "OK"

log = logging.getLogger()


class LightStreamerSubscription:
    """Represents a Subscription to be submitted to a Light Streamer Server."""

    def __init__(self, mode, items, fields, adapter='', snapshot=True, notify_new_values=False):
        self.item_names = items
        self._items_map = {}
        self.field_names = fields
        self.adapter = adapter
        self.mode = mode
        self.snapshot = snapshot
        self._listeners = []
        self.notify_new_values = notify_new_values

    @staticmethod
    def _decode(value, last):
        """Decode the field value according to Light Streamer Text Protocol specifications."""

        if value == "$":
            return u''
        elif value == "#":
            return None
        elif not value:
            return last
        elif value[0] in "#$":
            value = value[1:]

        return value

    def add_listener(self, listener):
        self._listeners.append(listener)

    def notify_update(self, item_line):
        """Invoked by LSClient each time Light Streamer Server pushes a new item event."""

        # tokenize the item line as sent by Light Streamer
        tokens = item_line.rstrip('\r\n').split('|')
        item_pos = int(tokens[0])
        item_name = self.item_names[item_pos - 1]

        if self.notify_new_values:
            # undecode just new keys
            undecoded_item = dict(list((k, v) for k, v in list(zip(self.field_names, tokens[1:])) if v != ''))
            item_values = undecoded_item

        else:
            # undecode new keys and set empty string for not send tokens
            undecoded_item = dict(list(zip(self.field_names, tokens[1:])))

            # retrieve the previous item stored into the map, if present, otherwise create a new empty dict
            curr_item = self._items_map.get(item_pos, {})

            # update the map with new values, merging with the previous ones if any
            self._items_map[item_pos] = dict(
                [
                    (k, self._decode(v, curr_item.get(k)))
                    for k, v in list(undecoded_item.items())
                ]
            )
            item_values = self._items_map[item_pos]

        # make an item info as a new event to be passed to listeners
        item_info = {
            'pos': item_pos,
            'name': item_name,
            'values': item_values,
        }

        # update each registered listener with new event
        for on_item_update in self._listeners:
            on_item_update(item_info)


class LightStreamerClient:
    """Manages the communication with Light Streamer Server."""

    def __init__(self, ls_username, ls_token, ls_url, adapter_set=''):
        self._ls_username = ls_username
        self._ls_token = ls_token
        self._ls_url = parse_url(ls_url)
        self._adapter_set = adapter_set
        self._session = {}
        self._subscriptions = {}
        self._current_subscription_key = 0
        self._stream_connection = None
        self._stream_connection_thread = None
        self._bind_counter = 0

    @staticmethod
    def _encode_params(params):
        """Encode the parameter for HTTP POST submissions, but only for non-empty values."""

        return urlencode(dict([(k, v) for (k, v) in iter(params.items()) if v])).encode("utf-8")

    def _call(self, base_url, url, params):
        """Open a network connection and performs HTTP Post with provided params."""

        # combines the "base_url" with the required "url" to be used for the specific request
        url = urljoin(base_url.geturl(), url)
        body = self._encode_params(params)
        log.debug("Making a request to <%s> with body <%s>", url, body)
        return _urlopen(url, data=body)

    def _set_control_link_url(self, custom_address=None):
        """Set the address to use for the Control Connection in such cases where Light Streamer is behind a Load
        Balancer."""

        if custom_address is None:
            self._control_url = self._ls_url
        else:
            parsed_custom_address = parse_url("//" + custom_address)
            # noinspection PyProtectedMember
            self._control_url = parsed_custom_address._replace(scheme=self._ls_url[0])

    def _control(self, params):
        """Create a Control Connection to send control commands that manage the content of Stream Connection."""

        params["LS_session"] = self._session["SessionId"]
        response = self._call(self._control_url, CONTROL_URL_PATH, params)
        decoded_response = response.readline().decode("utf-8").rstrip()
        log.debug("Server response: <%s>", decoded_response)
        return decoded_response

    def _read_from_stream(self):
        """Read a single line of content of the Stream Connection."""

        line = self._stream_connection.readline().decode("utf-8").rstrip()
        return line

    def connect(self):
        """Establish a connection to Light Streamer Server to create a new session."""

        log.debug("Opening a new session to <%s>", self._ls_url.geturl())
        self._stream_connection = self._call(
            self._ls_url,
            CONNECTION_URL_PATH,
            {
                "LS_adapter_set": self._adapter_set,
                "LS_user": self._ls_username,
                "LS_password": self._ls_token
            }
        )
        stream_line = self._read_from_stream()
        self._handle_stream(stream_line)

    def bind(self):
        """Replace a completely consumed connection in listening for an active Session."""

        log.debug("Binding to <%s>", self._control_url.geturl())
        self._stream_connection = self._call(
            self._control_url,
            BIND_URL_PATH,
            {
                "LS_session": self._session["SessionId"]
            }
        )

        self._bind_counter += 1
        stream_line = self._read_from_stream()
        self._handle_stream(stream_line)
        log.info("Bound to <%s>", self._control_url.geturl())

    def _handle_stream(self, stream_line):
        if stream_line == OK_CMD:
            log.info("Successfully connected to <%s>", self._ls_url.geturl())
            log.debug("Starting to handling real-time stream")
            # parsing session
            while True:
                next_stream_line = self._read_from_stream()
                if next_stream_line:
                    session_key, session_value = next_stream_line.split(":", 1)
                    self._session[session_key] = session_value
                else:
                    break

            # setup of the control link url
            self._set_control_link_url(self._session.get("ControlAddress"))

            # start a new thread to handle real time updates sent by Light Streamer Server on the stream connection
            self._stream_connection_thread = threading.Thread(
                name="StreamThread-{0}".format(self._bind_counter),
                target=self._receive
            )
            self._stream_connection_thread.daemon = True
            self._stream_connection_thread.start()
            log.info("Started handling of real-time stream")

        else:
            lines = self._stream_connection.readlines()
            lines.insert(0, stream_line)
            log.error("Server response error: \n%s", "\n".join(lines))
            raise IOError()

    def _join(self):
        """Await the natural StreamThread termination."""

        if self._stream_connection_thread:
            log.debug("Waiting for thread to terminate")
            self._stream_connection_thread.join()
            self._stream_connection_thread = None
            log.debug("Thread terminated")

    def disconnect(self):
        """Request to close the session previously opened with the connect() invocation."""

        if self._stream_connection is not None:
            log.debug("Closing session to <%s>", self._ls_url.geturl())
            _ = self._control({"LS_op": OP_DESTROY})
            # there is no need to explicitly close the connection, since it is handled by thread completion
            self._join()
            log.info("Closed session to <%s>", self._ls_url.geturl())
        else:
            log.warning("No connection to Light Streamer")

    def subscribe(self, subscription):
        """Perform a subscription request to Light Streamer Server."""

        # register the Subscription with a new subscription key
        self._current_subscription_key += 1
        self._subscriptions[self._current_subscription_key] = subscription

        # send the control request to perform the subscription
        log.debug("Making a new subscription request")
        server_response = self._control({
            "LS_Table": self._current_subscription_key,
            "LS_op": OP_ADD,
            "LS_snapshot": subscription.snapshot,
            "LS_data_adapter": subscription.adapter,
            "LS_mode": subscription.mode,
            "LS_schema": " ".join(subscription.field_names),
            "LS_id": " ".join(subscription.item_names)
        })
        if server_response == OK_CMD:
            log.info("Successfully subscribed ")
        else:
            log.warning("Subscription error")
        return self._current_subscription_key

    def unsubscribe(self, subscription_key):
        """Unregister the Subscription associated to the specified subscription_key."""

        log.debug("Making an unsubscription request")
        if subscription_key in self._subscriptions:
            server_response = self._control({
                "LS_Table": subscription_key,
                "LS_op": OP_DELETE
            })

            if server_response == OK_CMD:
                del self._subscriptions[subscription_key]
                log.info("Successfully unsubscribed")
            else:
                log.warning("Unsubscription error")
        else:
            log.warning("No subscription key %s found!", subscription_key)

    def unsubscribe_all(self):
        """Unregister all Subscriptions."""

        log.debug("Making unsubscription requests for all")
        for subscription_key in self._subscriptions.copy().keys():
            server_response = self._control({
                "LS_Table": subscription_key,
                "LS_op": OP_DELETE
            })

            if server_response == OK_CMD:
                del self._subscriptions[subscription_key]
                log.info("Successfully unsubscribed from " + str(subscription_key))
            else:
                log.warning("Unsubscription error")
                log.warning("Server response: " + str(server_response))

    def _forward_update_message(self, update_message):
        """Forwards the real time update to the relative Subscription instance for further dispatching to its
        listeners."""

        log.debug("Received update message: <%s>", update_message)
        # noinspection PyBroadException
        try:
            tok = update_message.split(',', 1)
            table, item = int(tok[0]), tok[1]
            if table in self._subscriptions:
                self._subscriptions[table].notify_update(item)
            else:
                log.warning("No subscription found!")
        except Exception:
            print(traceback.format_exc())

    def _receive(self):
        rebind = False
        receive = True
        while receive:
            log.debug("Waiting for a new message")
            # noinspection PyBroadException
            try:
                message = self._read_from_stream()
                log.debug("Received message: <%s>", message)
                if not message.strip():
                    message = None
            except Exception:
                log.error("Communication error")
                print(traceback.format_exc())
                message = None

            if message is None:
                receive = False
                log.warning("No new message received")
            elif message == PROBE_CMD:
                # skipping the PROBE message, keep on receiving messages
                log.debug("PROBE message")
            elif message.startswith(ERROR_CMD):
                # terminate the receiving loop on ERROR message
                receive = False
                log.error("ERROR")
            elif message.startswith(LOOP_CMD):
                # terminate the receiving loop on LOOP message
                # a complete implementation should proceed with a rebind of the session
                log.debug("LOOP")
                receive = False
                rebind = True
            elif message.startswith(SYNC_ERROR_CMD):
                # terminate the receiving loop on SYNC ERROR message
                # a complete implementation should create a new session and re-subscribe to all the old items and
                # relative fields
                log.error("SYNC ERROR")
                receive = False
            elif message.startswith(END_CMD):
                # terminate the receiving loop on END message
                # the session has been forcibly closed on the server side a complete implementation should handle the
                # "cause_code" if present
                log.info("Connection closed by the server")
                receive = False
            elif message.startswith("Preamble"):
                # skipping Preamble message, keep on receiving messages
                log.debug("Preamble")
            else:
                self._forward_update_message(message)

        if not rebind:
            log.debug("No rebind to <%s>, clearing internal session data", self._ls_url.geturl())
            # clear internal data structures for session and subscriptions management
            self._stream_connection = None
            self._session.clear()
            self._subscriptions.clear()
            self._current_subscription_key = 0
        else:
            log.debug("Binding to this active session")
            self.bind()
