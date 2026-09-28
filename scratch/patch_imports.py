with open("frontend/src/App.jsx", "r") as f:
    content = f.read()

target = """  Loader2, Settings, ClipboardList, Clock, XCircle // <-- ADDED for the Agentic Processing Tracker
} from 'lucide-react';"""

replacement = """  Loader2, Settings, ClipboardList, Clock, XCircle, ArrowRight, ArrowLeft, LayoutDashboard
} from 'lucide-react';"""

content = content.replace(target, replacement)

with open("frontend/src/App.jsx", "w") as f:
    f.write(content)
print("Updated lucide-react imports")
