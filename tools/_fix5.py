with open(r"J:\Coding\OnekeyOpenClaw\installer\install.py","rb") as f:
    raw = f.read()
target_log = b'written (HKCU)'
idx = raw.find(target_log)
# go back to find log(
start = raw.rfind(b'log("', 0, idx)
end = raw.find(b'")', idx) + 2
old = raw[start:end]
new = old + b' - WARNING: will run as current user, admin required for DISM'
raw = raw.replace(old, new, 1)
with open(r"J:\Coding\OnekeyOpenClaw\installer\install.py","wb") as f:
    f.write(raw)
print("Issue 5 done")
