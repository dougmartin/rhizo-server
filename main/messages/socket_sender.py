import json
import datetime
import gevent
from geventwebsocket.websocket import WebSocketError


# The SocketSender runs a greenlet that sends messages (temporarily stored in DB) out to websockets.
class SocketSender(object):

    def __init__(self):
        self.connections = []  # list of WebSocketConnection objects

    # register a client (possible message recipient)
    def register(self, ws_conn):
        print('client registered (%s)' % ws_conn)
        self.connections.append(ws_conn)

    # unregister a client (e.g. after it has been closed
    def unregister(self, ws_conn):
        print('client unregistered (%s)' % ws_conn)
        self.connections.remove(ws_conn)

    # send a message to a specific client (using websocket connection specified in ws_conn)
    def send(self, ws_conn, message):
        if not ws_conn.ws.closed:  # if it was recently closed, it may still be in the list of connections; it should be removed as soon as the manage_web_socket function terminates
            try:
                ws_conn.ws.send(message)
            except:  # WebSocketError:
                print('unable to send to websocket (%s)' % ws_conn)

    # send an error message back to a client
    def send_error(self, ws_conn, message_text):
        message_struct = {
            'type': 'error',
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
            'parameters': {'message': message_text},
        }
        self.send(ws_conn, json.dumps(message_struct))

    # this function sits in a loop, waiting for messages that need to be sent out to subscribers
    def send_messages(self):
        from main.app import message_queue
        debug_mode = False  # fix(later): global verbosity settings?
        while True:

            # get all messages since the last message we processed
            messages = message_queue.receive()
            for message in messages:
                if debug_mode:
                    print('message type: %s, folder: %s' % (message.type, message.folder_id))

                # handle special messages aimed at this module
                if message.type == 'requestProcessStatus':
                    self.send_process_status()

                # all other messages are passed to clients managed by this process
                else:
                    for ws_conn in self.connections:
                        if client_is_subscribed(message, ws_conn, debug_mode):
                            message_struct = {
                                'type': message.type,
                                'timestamp': message.timestamp.isoformat() + 'Z',
                                'parameters': json.loads(message.parameters)
                            }
                            gevent.spawn(self.send, ws_conn, json.dumps(message_struct))
                            if debug_mode:
                                if ws_conn.controller_id:
                                    print('sending message to controller; type: %s' % message.type)
                                else:
                                    print('sending message to browser; type: %s' % message.type)

    # spawn a greenlet that sends messages to clients
    def start(self):
        gevent.spawn(self.send_messages)

    # send information about the current process as a message to the system folder
    # (in a multi-process environment, each process has an instance of this class)
    # fix(clean): move elsewhere?
    def send_process_status(self):
        from main.app import db  # import here to avoid import loop
        from main.app import message_queue  # import here to avoid import loop
        from main.app import process_id  # import here to avoid import loop
        from main.resources.resource_util import find_resource  # import here to avoid import loop
        connections = []
        for ws_conn in self.connections:
            connections.append({
                'connected': ws_conn.connected(),
                'controller_id': ws_conn.controller_id,
                'user_id': ws_conn.user_id,
                'auth_method': ws_conn.auth_method,
                'process_id': process_id,
                'subscriptions': [s.as_dict() for s in ws_conn.subscriptions],
            })
        parameters = {
            'process_id': process_id,
            'clients': connections,  # fix(later): rename to connections?
            'db_pool': db.engine.pool.size(),
            'db_conn': db.engine.pool.checkedout(),
        }
        system_folder_id = find_resource('/system').id
        message_queue.add(system_folder_id, 'processStatus', parameters)


# returns True if the given message should be sent to the given client (based on its current subscriptions)
# fix(clean): move into wsConn?
def client_is_subscribed(message, ws_conn, debug_mode):
    if message.sender_controller_id:
        if ws_conn.controller_id and message.sender_controller_id == ws_conn.controller_id:
            return False  # don't bounce messages back to controller sender
        if ws_conn.user_id and message.sender_user_id == ws_conn.user_id:
            return False  # don't bounce messages back to user sender (note this prevents sending message from one browser tab to another)
    for subscription in ws_conn.subscriptions:
        if debug_mode:
            print('    client subscription folders: %s, type: %s' % (subscription.folder_ids, subscription.message_type))
        if subscription.matches(message):
            return True
    return False


# clear controller connection status on startup
def clear_web_sockets():
    # fix(soon): what if we spin up another process after some are connected?
    from main.resources.models import ControllerStatus  # would like to do at top, but creates import loop in __init__
    from main.app import db  # would like to do at top, but creates import loop in __init__
    controller_statuses = ControllerStatus.query.all()
    for controller_status in controller_statuses:
        controller_status.web_socket_connected = False
    db.session.commit()
    db.session.close()
