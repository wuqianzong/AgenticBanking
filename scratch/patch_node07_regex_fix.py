with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = r"""        # Clean both single and double bracket forms from ALL text fields globally
        def _strip_deeplink(obj):
            if isinstance(obj, str):
                return re.sub(f"\\{{1,2}}deeplink-{destination_id}\\}}{{1,2}}", "", obj).strip()"""

replacement = r"""        # Clean both single and double bracket forms from ALL text fields globally
        def _strip_deeplink(obj):
            if isinstance(obj, str):
                return re.sub(r"\{{1,2}deeplink-" + destination_id + r"\}{1,2}", "", obj).strip()"""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Fixed regex f-string issue")
