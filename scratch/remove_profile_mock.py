with open("frontend/src/App.jsx", "r") as f:
    content = f.read()

target = """      case 'profile-personal-details':
        return (
          <div className="w-full text-left space-y-4">
            <div className="flex flex-col items-center justify-center py-4">
              <div className="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 mb-3 shadow-inner">
                <span className="text-2xl font-bold">{preferences.nickname?.[0] || 'C'}</span>
              </div>
              <h3 className="font-semibold text-slate-800">{preferences.nickname || 'Customer'}</h3>
              <p className="text-sm text-slate-500">Member since 2021</p>
            </div>
            
            <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100">
                <div className="text-xs text-slate-500 mb-1">Email Address</div>
                <div className="text-sm font-medium text-slate-800">hello@example.com</div>
              </div>
              <div className="px-4 py-3 border-b border-slate-100">
                <div className="text-xs text-slate-500 mb-1">Mobile Number</div>
                <div className="text-sm font-medium text-slate-800">+65 9123 4567</div>
              </div>
              <div className="px-4 py-3">
                <div className="text-xs text-slate-500 mb-1">Residential Address</div>
                <div className="text-sm font-medium text-slate-800">123 Orchard Road<br/>#10-01<br/>Singapore 238881</div>
              </div>
            </div>
            
            <button className="w-full bg-blue-600 text-white font-medium py-3 rounded-xl shadow-sm hover:bg-blue-700 transition-colors">
              Edit Details
            </button>
          </div>
        );
        
      case 'security-settings':"""

replacement = """      case 'security-settings':"""

content = content.replace(target, replacement)
with open("frontend/src/App.jsx", "w") as f:
    f.write(content)
print("Removed profile mock from frontend")
