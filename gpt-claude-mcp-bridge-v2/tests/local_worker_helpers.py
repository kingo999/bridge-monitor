from pathlib import Path
import tempfile


def packet_data(task_id='sample'):
    return dict(task_id=task_id, category='isolated_repair', objective='Stable unique values',
                constraints='None becomes empty; preserve order and input; return fresh list',
                source='def stable_unique(items): return sorted(set(items))',
                context='Standalone Python function', diagnostic='None raises TypeError',
                versions={'Python': '3.13'}, references=[], output_form='one replacement function',
                context_id='synthetic-v1', approval=dict(classification='synthetic',
                    approval_ref='user-approved-local00-pilot', audit_content_approved=True),
                checks=[dict(check_id='compile', kind='compile', contract='py_compile succeeds', method='independent py_compile'),
                        dict(check_id='behaviour', kind='behaviour', contract='None, empty, stable order, no mutation, fresh list', method='independent executable assertions')])


class TempWorld:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.root = self.home / '.gpt-architect-bridge-local00'
    def close(self):
        self.temp.cleanup()


class HTTPFixture:
    def __init__(self, routes=None):
        import http.server
        import threading
        import json
        from bridge.local_worker.settings import MODEL,DIGEST
        owner=self
        self.requests=[]
        self.routes=routes or {}
        self.default_tags={'models':[{'name':MODEL,'digest':DIGEST}]}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.reply()
            def do_POST(self):self.reply()
            def reply(self):
                import time
                length=int(self.headers.get('Content-Length','0'))
                body=json.loads(self.rfile.read(length)) if length else None
                owner.requests.append((self.command,self.path,body))
                status,data,delay=owner.routes.get(self.path,(200,owner.default_tags if self.path=='/api/tags' else {'models':[]},0))
                if callable(data):data=data(body)
                raw=data if isinstance(data,bytes) else json.dumps(data).encode()
                time.sleep(delay)
                try:
                    self.send_response(status);self.send_header('Content-Length',str(len(raw)))
                    self.send_header('Location','http://127.0.0.1/forbidden');self.end_headers();self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def dial(self):return self.server.server_address
    @property
    def generation_count(self):return sum(path=='/api/generate' for _,path,_ in self.requests)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
