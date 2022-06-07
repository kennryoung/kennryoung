import asyncio
import ssl
import os
import sys
import json

from threading import Thread, Lock
from typing import Any

from tornado import httputil
from util.util import Util
from util.log import configure_logging
import cfgmgr.config_manager as cfg
import eii.msgbus as mb
import tornado.websocket
import tornado.web
import tornado.ioloop

log = None
stop_subscribing = False

# Config manager initialization
ctx = cfg.ConfigMgr()
topics_list = []
topic_config_list = []

class eiiWebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application: tornado.web.Application, request: httputil.HTTPServerRequest,
                 **kwargs: Any) -> None:
        self.messageBus: mbsubscriber = kwargs.pop('messagebus')
        super().__init__(application, request, **kwargs)
        self.log = configure_logging("DEBUG", "eiiWebSocketHandler", True)

    async def open(self, topic):
        self.topic = topic
        if topic:
            if self.messageBus.is_topic_available(topic):
                current_loop = tornado.ioloop.IOLoop.current()
                self.log.debug("Calling subscribe")
                self.subscriber = self.messageBus.get_subscriber(topic)
                current_loop.add_callback(self.process_data)
                self.log.debug("Done calling both")
            else:
                self.send_error("Topic not found")
                self.close()
        pass
    async def process_data(self):
        try:
            async for data in self.subscriber.start_subscribe():
                self.write_message({"metadata": data[0]})
        except tornado.websocket.WebSocketClosedError:
            self.log.debug("Websocket closed for write")
        except:
            self.log.error(sys.exc_info())


    def on_close(self):
        self.log.debug("on_close called")
        self.subscriber.stop_subscribing()
        del(self.subscriber)

    def check_origin(self, origin: str) -> bool:
        return True

class Subscriber:
    def __init__(self, topic:str, mb):
        self.topic = topic
        self._keep_subscribing = True
        self.mb = mb
        self._dev_mode = mb._dev_mode
        self.log = configure_logging(os.environ['PY_LOG_LEVEL'], self.__class__.__name__, self._dev_mode)

    async def start_subscribe(self):
        if self.mb.is_topic_available(self.topic):
            msgbus_cfg = self.mb.msgbus_cfg_dict[self.topic]
            msgbus = mb.MsgbusContext(msgbus_cfg)
            self.subscriber = msgbus.new_subscriber(self.topic)
            self.log.info("Subscribed for {} using {}".format(self.topic, msgbus_cfg))
            while self._keep_subscribing:
                try:
                    loop = asyncio.get_event_loop()
                    metadata, blob = await loop.run_in_executor(None, self.subscriber.recv)
                    # Possible state changed during await
                    if self._keep_subscribing:
                        yield (metadata, blob)
                    else:
                        break
                    # self.log.debug("Done Yielding")
                except Exception:
                    self.log.error(sys.exc_info())
                    self._keep_subscribing = False
            self.subscriber.close()
            self.log.debug("Closed subscriber")

    def stop_subscribing(self):
        self.log.debug("Stop subscribing called")
        self._keep_subscribing = False

class mbsubscriber:
    def __init__(self):
        self._dev_mode = ctx.is_dev_mode()
        self.msgbus_cfg_dict = {}

        num_of_subscribers = ctx.get_num_subscribers()
        for index in range(num_of_subscribers):
            # Fetching subscriber element based on index
            sub_ctx = ctx.get_subscriber_by_index(index)
            # Fetching msgbus config of subscriber
            msgbus_cfg = sub_ctx.get_msgbus_config()
            # Fetching topics of subscriber
            topic = sub_ctx.get_topics()[0]
            # Adding topic & msgbus_config to
            # topic_config tuple
            self.msgbus_cfg_dict[topic] = msgbus_cfg
            topic_config = (topic, msgbus_cfg)
            topic_config_list.append(topic_config)
            topics_list.append(topic)

        self.log = configure_logging(os.environ['PY_LOG_LEVEL'], self.__class__.__name__, self._dev_mode)
        self._keep_subscribing = True

    def is_topic_available(self, topic_name):
        """
        Method to see if topic_name can be subscribe
        :param topic_name: Topic to check for
        :return: True if it's allowed, False otherwise
        """
        if self.msgbus_cfg_dict:
            return topic_name in self.msgbus_cfg_dict
        else:
            return False


    def get_subscriber(self, topic=None):
        return Subscriber(topic, self)

def main():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    json_config = ctx.get_app_config()
    dev_mode = ctx.is_dev_mode()
    config_client = json.dumps(json_config.get_dict())
    # Validating config against schema
    with open('./schema.json', "rb") as infile:
        schema = infile.read()
        if (Util.validate_json(schema, config_client)) is not True:
            sys.exit(1)
    log_debug_mode = os.environ.get('PY_LOG_LEVEL','debug').lower() == 'debug'
    global log, stop_subscribing
    log = configure_logging(os.environ['PY_LOG_LEVEL'], 'eii_websocket', dev_mode)
    mb = mbsubscriber()
    app = tornado.web.Application([
        (r'/ws/(.*)',eiiWebSocketHandler, dict(messagebus=mb),
         )

    ],
    debug=log_debug_mode)
    app.listen(json_config.cfg.get('port', 35331))
    current_loop = tornado.ioloop.IOLoop.current()
    # current_loop.add_callback(check_message, mb, ws_write_func_dict, log)
    try:
        current_loop.start()
    except KeyboardInterrupt:
        log.info("Quitting..")

if __name__ == "__main__":
    main()
