"""One private stdlib HTTP exchange; launched with Python -I, never MCP stdout."""
import base64
import http.client
import ipaddress
import json
import socket
import sys
import time


def allowed_peer(value):
    address=ipaddress.ip_address(value)
    if getattr(address,'ipv4_mapped',None):address=address.ipv4_mapped
    return not (address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local)


def main():
    request=json.loads(sys.stdin.buffer.read(262145))
    started=time.monotonic()
    outcome=dict(phase='not_sent',code='ok',status=None,peer_ip=None,body_b64='',data=None,truncated=False,elapsed_s=0.0)
    conn=None
    try:
        host=request.get('dial_host') or request['host']
        port=request.get('dial_port') or 11434
        deadline=started+request['timeout_s']
        addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
        sock=None
        for family,kind,proto,_,address in addresses:
            if not request.get('test_dial') and not allowed_peer(address[0]):continue
            trial=socket.socket(family,kind,proto)
            try:
                trial.settimeout(max(.001,deadline-time.monotonic()))
                trial.connect(address);sock=trial;break
            except OSError:
                trial.close()
        if sock is None:raise OSError('no reachable allowed address')
        outcome['peer_ip']=sock.getpeername()[0]
        conn=http.client.HTTPConnection(request['host'],11434)
        conn.sock=sock
        headers={'Host':f"[{request['host']}]:11434" if ':' in request['host'] else request['host']+':11434','Connection':'close'}
        body=json.dumps(request['body'],ensure_ascii=False).encode() if request['body'] is not None else None
        if body is not None:headers['Content-Type']='application/json'
        outcome['phase']='may_have_started'
        conn.request(request['method'],request['path'],body=body,headers=headers)
        response=conn.getresponse()
        raw=response.read(request['max_bytes']+1)
        outcome['status']=response.status
        outcome['truncated']=len(raw)>request['max_bytes']
        raw=raw[:request['max_bytes']]
        outcome['body_b64']=base64.b64encode(raw).decode('ascii')
        if outcome['truncated']:
            outcome['code']='response_too_large'
        else:
            outcome['phase']='terminal_received'
            try:
                data=json.loads(raw)
                if not isinstance(data,dict):raise ValueError('object expected')
                outcome['data']=data
            except (ValueError,UnicodeError):outcome['code']='response_malformed'
    except (OSError,ValueError,http.client.HTTPException):
        outcome['code']='transport_error'
    finally:
        if conn is not None:conn.close()
    outcome['elapsed_s']=time.monotonic()-started
    sys.stdout.write(json.dumps(outcome,separators=(',',':'),allow_nan=False))


if __name__=='__main__':main()
