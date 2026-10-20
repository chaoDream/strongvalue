#!/usr/bin/env python3
"""Local operator CLI; credentials are not managed by browser-supplied roles."""
import argparse
import getpass
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
from backend import store
from backend.main import password_hash

p = argparse.ArgumentParser(description='QiangQuote 本地账号管理')
p.add_argument('action', choices=['create-user', 'reset-password'])
p.add_argument('username')
p.add_argument('--tenant', default='local')
p.add_argument('--role', choices=['admin', 'manager', 'estimator', 'reviewer'], default='estimator')
args = p.parse_args()
password = getpass.getpass('新密码（至少12位）：')
if len(password) < 12 or password != getpass.getpass('再次输入：'):
    sys.exit('密码不足12位或两次输入不一致')
with store.transaction() as c:
    u = c.execute('SELECT * FROM users WHERE username=?', (args.username,)).fetchone()
    if args.action == 'create-user':
        if u:
            sys.exit('账号已存在')
        c.execute('INSERT INTO users(id,tenant,username,password,role) VALUES(?,?,?,?,?)', (store.uid(), args.tenant, args.username, password_hash(password), args.role))
    else:
        if not u:
            sys.exit('账号不存在')
        c.execute('UPDATE users SET password=? WHERE id=?', (password_hash(password), u['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))
print('账号已更新。密码重置后旧会话已注销。')
