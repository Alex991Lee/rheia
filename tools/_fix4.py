f=open(r"J:\Coding\OnekeyOpenClaw\ui\terminal\installui.py","r",encoding="utf-8")
c=f.read()
f.close()
old="if os.path.exists(progressFile):\n            _launchWizard()\n            return\n        resumeMode = False"
new="if os.path.exists(progressFile):\n            try:\n                with open(progressFile, \"r\", encoding=\"utf-8\") as f:\n                    data = json.load(f)\n                if 8 in data:\n                    _launchWizard()\n                    return\n            except Exception:\n                pass\n        resumeMode = False"
c=c.replace(old,new,1)
f=open(r"J:\Coding\OnekeyOpenClaw\ui\terminal\installui.py","w",encoding="utf-8")
f.write(c)
f.close()
print("Issue 4 done")
