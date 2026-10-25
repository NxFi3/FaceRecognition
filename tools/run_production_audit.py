"""Disposable loopback production-mode verification; NEVER targets production data.

Requires Docker, certutil, Chromium, Playwright and desktop/service dependencies.
Creates isolated PostgreSQL/Redis/SMTP/web/TLS containers, synthetic accounts, and
one temporary CA trust entry; removes them in finally. No public ports except a
loopback HTTPS port. No live bank, public ACME or external SMTP is exercised.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
SMTP_SERVER = r'''
import asyncio, pathlib, ssl, uuid
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/certs/server.pem','/certs/server-key.pem')
async def client(reader,writer):
    writer.write(b'220 isolated-audit ESMTP\r\n'); await writer.drain()
    encrypted=False
    try:
        while line:=await reader.readline():
            command=line.split(b' ',1)[0].strip().upper()
            if command in (b'EHLO',b'HELO'):
                writer.write(b'250-isolated-audit\r\n'+(b'' if encrypted else b'250-STARTTLS\r\n')+b'250 SIZE 1048576\r\n')
            elif command==b'STARTTLS':
                writer.write(b'220 Begin TLS\r\n');await writer.drain()
                await writer.start_tls(context);encrypted=True;continue
            elif command in (b'MAIL',b'RCPT',b'RSET',b'NOOP'):
                writer.write(b'250 OK\r\n')
            elif command==b'DATA':
                writer.write(b'354 End with dot\r\n');await writer.drain()
                data=bytearray()
                while row:=await reader.readline():
                    if row==b'.\r\n':break
                    data.extend(row[1:] if row.startswith(b'..') else row)
                    if len(data)>1048576:raise ValueError('Audit SMTP size bound')
                p=pathlib.Path('/spool')/(uuid.uuid4().hex+'.eml')
                temporary=p.with_suffix('.tmp');temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(p)
                writer.write(b'250 Captured synthetic message\r\n')
            elif command==b'QUIT':
                writer.write(b'221 Bye\r\n');await writer.drain();break
            else:writer.write(b'502 Unsupported\r\n')
            await writer.drain()
    finally:
        writer.close()
async def main():
    server=await asyncio.start_server(client,'0.0.0.0',8025)
    async with server:await server.serve_forever()
asyncio.run(main())
'''


def command(args, *, data=None, timeout=180):
    result = subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Do not dump private fixtures, environment contents or token-bearing URLs.
        raise RuntimeError('Audit subprocess failed: ' + ' '.join(args[:3]))
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='nxfi-audit:test')
    parser.add_argument('--port', type=int, default=18443)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/screenshots/audit')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged loopback port')
    prefix = 'nxfi-audit-' + secrets.token_hex(4)
    containers, networks = [], []
    nss = Path.home()/'.pki/nssdb'
    trusted = False
    with tempfile.TemporaryDirectory(prefix='nxfi-production-audit-') as working:
        work = Path(working)
        certs, spool, releases = [work/name for name in ('certs','spool','releases')]
        for path in (certs,spool,releases):path.mkdir(mode=0o755)
        ca_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,prefix+' isolated CA')])
        now = datetime.now(timezone.utc)
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=False,decipher_only=False),critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
        server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        smtp_name=prefix+'-smtp'
        server=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
            .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.DNSName(smtp_name)]),critical=False)
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(ca_key,hashes.SHA256()))
        (certs/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (certs/'server.pem').write_bytes(server.public_bytes(serialization.Encoding.PEM)+ca.public_bytes(serialization.Encoding.PEM))
        (certs/'server-key.pem').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        for path in certs.iterdir():path.chmod(0o644)  # Host parent is private0700; mounts are read-only in isolated containers.
        signer=ed25519.Ed25519PrivateKey.generate()
        private=base64.b64encode(signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode()
        public=base64.b64encode(signer.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        pg_password, admin_password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        pg_name, redis_name, web_name, proxy_name = [prefix+'-'+name for name in ('db','redis','web','tls')]
        internal, edge=prefix+'-internal',prefix+'-edge'
        origin='https://localhost:'+str(args.port)
        env = {'DJANGO_SETTINGS_MODULE':'service.settings','DJANGO_SECRET_KEY':secrets.token_urlsafe(64),'PUBLIC_URL':origin,
            'DATABASE_URL':f'postgresql://face_service:{pg_password}@{pg_name}:5432/face_service',
            'REDIS_URL':f'redis://{redis_name}:6379/0','LICENSE_SIGNING_KEY':private,
            'BACKUP_MASTER_KEY':base64.b64encode(os.urandom(32)).decode(), 'SMTP_HOST':smtp_name,'SMTP_PORT':'8025',
            'SMTP_FROM':'audit@example.invalid','SSL_CERT_FILE':'/run/audit-ca.pem','RELEASE_ROOT':'/data/releases',
            'NO_PROXY':','.join(('localhost','127.0.0.1',web_name,pg_name,redis_name,smtp_name)),
            'MANUAL_PAYMENT_INSTRUCTIONS':'Synthetic audit only — no real payment. Do not transfer money.'}
        envfile=work/'web.env';envfile.write_text(''.join(k+'='+v+'\n' for k,v in env.items()));envfile.chmod(0o600)
        pgenv=work/'postgres.env';pgenv.write_text(f'POSTGRES_USER=postgres\nPOSTGRES_PASSWORD={admin_password}\nPOSTGRES_DB=face_service\nPOSTGRES_APP_PASSWORD={pg_password}\n');pgenv.chmod(0o600)
        (work/'smtp.py').write_text(SMTP_SERVER)
        (work/'Caddyfile').write_text('https://localhost:443 {\n tls /certs/server.pem /certs/server-key.pem\n reverse_proxy '+web_name+':8000 {\n header_up X-Forwarded-For {remote_host}\n }\n}\n')
        fixture={'base_url':origin,'ca_file':str(certs/'ca.pem'),'smtp_spool':str(spool),
            'owner_email':'audit-owner@example.invalid','owner_password':secrets.token_urlsafe(24)+'!7xA',
            'school_email':'audit-school@example.invalid','school_password':secrets.token_urlsafe(24)+'!8yB',
            'other_school_email':'audit-other@example.invalid','other_school_password':secrets.token_urlsafe(24)+'!9zC',
            'public_key':public,'synthetic_disposable':True,'working_directory':str(work/'client')}
        try:
            for name, opts in ((internal,['--internal']),(edge,[])):
                command(['docker','network','create',*opts,name]);networks.append(name)
            def run(name, image, extra, cmd=(), network=None):
                command(['docker','run','-d','--name',name,'--network',network or internal,*extra,image,*cmd]);containers.append(name)
            run(pg_name,'face-subscription-postgres:17',['--env-file',str(pgenv)])
            run(redis_name,'redis:7-alpine',[],['redis-server','--maxmemory','128mb','--maxmemory-policy','noeviction'])
            run(smtp_name,args.image,['--user',str(os.getuid()),'--mount',f'type=bind,src={certs},dst=/certs,readonly','--mount',f'type=bind,src={spool},dst=/spool','--mount',f'type=bind,src={work/"smtp.py"},dst=/smtp.py,readonly'],['python','/smtp.py'])
            # TCP readiness waits for the final server, not the initializer's socket.
            for _ in range(60):
                result=subprocess.run(['docker','exec',pg_name,'pg_isready','-h','127.0.0.1','-U','postgres','-d','face_service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if result.returncode==0:break
                time.sleep(.25)
            else:raise RuntimeError('Disposable PostgreSQL did not start')
            run(web_name,args.image,['--env-file',str(envfile),'--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=512m,mode=1777','--security-opt','no-new-privileges:true','--cap-drop','ALL',
                '--mount',f'type=bind,src={certs/"ca.pem"},dst=/run/audit-ca.pem,readonly','--volume','/data/backups','--mount',f'type=bind,src={releases},dst=/data/releases,readonly'])
            run(proxy_name,'caddy:2-alpine',['-e','NO_PROXY='+','.join(('localhost','127.0.0.1',web_name)),'--mount',f'type=bind,src={certs},dst=/certs,readonly','--mount',f'type=bind,src={work/"Caddyfile"},dst=/etc/caddy/Caddyfile,readonly','-p',f'127.0.0.1:{args.port}:443'],network=edge)
            command(['docker','network','connect',internal,proxy_name])
            import requests
            session=requests.Session();session.trust_env=False
            probe_problem = ''
            for _ in range(100):
                try:
                    probe=session.get(origin+'/health/',verify=str(certs/'ca.pem'),timeout=2)
                    if probe.status_code==200:break
                    probe_problem='HTTP '+str(probe.status_code)
                except requests.RequestException as error:
                    probe_problem=type(error).__name__+': '+str(error)[:400]
                time.sleep(.3)
            else:
                print('Last local HTTPS probe: '+probe_problem,file=os.sys.stderr)
                for component in (web_name,proxy_name):
                    log=subprocess.run(['docker','logs','--tail','25',component],capture_output=True,text=True)
                    diagnostic=log.stdout+log.stderr
                    for secret in (*env.values(),pg_password,admin_password,fixture['owner_password'],fixture['school_password']):
                        if secret:diagnostic=diagnostic.replace(secret,'[redacted]')
                    print(component+' startup diagnostic: '+diagnostic, file=os.sys.stderr)
                raise RuntimeError('Production-mode web health did not become ready')
            seed="""
import json,sys,django
from unittest.mock import patch
django.setup()
from django.core.management import call_command
from service.models import Plan
v=json.loads(sys.stdin.read())
with patch('service.management.commands.provision_owner.getpass',return_value=v['owner_password']):
 call_command('provision_owner',email=v['owner_email'],verbosity=0)
p=Plan.objects.create(slug='audit-synthetic',name='طرح ساختگی ممیزی — قیمت آزمایشی',price_irr=90000000,duration_days=365,student_limit=900,device_limit=1,is_active=True,is_public=True)
print('AUDIT_PLAN_ID='+str(p.pk))
"""
            seeded=command(['docker','exec','-i',web_name,'python','-c',seed],data=json.dumps(fixture))
            fixture['plan_id']=int(seeded.split('AUDIT_PLAN_ID=')[1].splitlines()[0]);fixture['price_irr']=90000000
            fixture['docker_web']=web_name
            fixture['audit_proxy_container']=proxy_name
            # Read-only operational evidence, never dumps DB passwords or environment.
            evidence=command(['docker','exec',web_name,'python','-c',"import django,json,os;django.setup();from django.db import connection;from django.conf import settings;c=connection.cursor();c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user');print(json.dumps({'testing':settings.TESTING,'debug':settings.DEBUG,'db_vendor':connection.vendor,'db_privileges':c.fetchone(),'redis_backend':settings.CACHES['default']['BACKEND'],'uid':os.getuid(),'secure_cookies':settings.SESSION_COOKIE_SECURE}))"])
            observed=json.loads(evidence)
            if (observed['testing'] or observed['debug'] or observed['db_vendor']!='postgresql'
                    or any(observed['db_privileges']) or observed['uid']!=10001
                    or not observed['secure_cookies'] or 'redis.RedisCache' not in observed['redis_backend']):
                raise RuntimeError('Disposable stack did not satisfy production-mode checks')
            fixture_path=work/'fixture.json';fixture_path.write_text(json.dumps(fixture));fixture_path.chmod(0o600)
            nss.mkdir(parents=True,exist_ok=True)
            if not (nss/'cert9.db').exists():command(['certutil','-N','--empty-password','-d','sql:'+str(nss)])
            command(['certutil','-A','-d','sql:'+str(nss),'-n',prefix,'-t','C,,','-i',str(certs/'ca.pem')]);trusted=True
            args.output.mkdir(parents=True,exist_ok=True)
            (args.output/'stack-evidence.json').write_text(evidence+'\n')
            result=subprocess.run([os.sys.executable,'-m','tools.verify_production_workflow','--fixture',str(fixture_path),'--output',str(args.output)],cwd=ROOT)
            if result.returncode:raise RuntimeError('Production browser/client workflow failed; only synthetic fixture was used')
            # Exercise the real SMTP backend after financial commit, outside locks.
            delivery=command(['docker','exec',web_name,'python','-m','service.manage','flush_commercial_emails','--limit','20'])
            if 'deferred: 0' not in delivery:
                raise RuntimeError('Synthetic notification delivery to the local STARTTLS sink failed')
            observed['notification_dispatch']=delivery
            (args.output/'stack-evidence.json').write_text(json.dumps(observed,indent=2)+'\n')
        finally:
            if trusted:
                subprocess.run(['certutil','-D','-d','sql:'+str(nss),'-n',prefix],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for name in reversed(containers):
                subprocess.run(['docker','container','stop','--time','3',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                subprocess.run(['docker','container','rm','--volumes',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for name in reversed(networks):
                subprocess.run(['docker','network','rm',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('Production audit completed; disposable containers and CA entry removed.')


if __name__=='__main__':main()
