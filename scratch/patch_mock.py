with open("frontend/src/App.jsx", "r") as f:
    content = f.read()

target = """  const renderDeeplinkMockScreen = () => {
    if (!activeDeeplink) return null;
    return (
      <div className="absolute inset-0 bg-slate-50 z-50 flex flex-col">
        <div className="bg-white border-b border-slate-100 flex items-center px-4 py-3 sticky top-0 z-10 shadow-sm">
          <button onClick={() => setActiveDeeplink(null)} className="p-2 -ml-2 text-slate-500 hover:text-slate-700 transition-colors rounded-full hover:bg-slate-50">
            <ArrowLeft size={20} />
          </button>
          <h2 className="text-base font-semibold text-slate-800 ml-2">{activeDeeplink.label}</h2>
        </div>
        <div className="flex-1 overflow-y-auto p-4 flex flex-col items-center justify-center text-center">
          <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mb-4">
            <LayoutDashboard size={28} className="text-blue-500" />
          </div>
          <h3 className="text-lg font-medium text-slate-800 mb-2">{t(preferences.language, "deeplink.mockScreenTitle")}</h3>
          <p className="text-sm text-slate-500 max-w-[250px] mb-6">
            {(() => {
              const [before, after] = t(preferences.language, "deeplink.navigatedTo").split('{route}');
              return <>{before}<span className="font-semibold text-slate-700">{activeDeeplink.route}</span>{after}</>;
            })()}
          </p>
          <div className="w-full max-w-xs bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-left">
            <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">{t(preferences.language, "deeplink.destinationData")}</div>
            <div className="flex justify-between items-center py-2 border-b border-slate-50">
              <span className="text-sm text-slate-500">{t(preferences.language, "deeplink.destinationId")}</span>
              <span className="text-sm font-medium text-slate-700">{activeDeeplink.destinationId}</span>
            </div>
            <div className="flex justify-between items-center py-2">
              <span className="text-sm text-slate-500">{t(preferences.language, "deeplink.route")}</span>
              <span className="text-sm font-medium text-slate-700">{activeDeeplink.route}</span>
            </div>
          </div>
        </div>
      </div>
    );
  };"""

with open("scratch/mock_pages.jsx", "r") as f:
    mock_logic = f.read()

replacement = mock_logic + """
  const renderDeeplinkMockScreen = () => {
    if (!activeDeeplink) return null;
    return (
      <div className="absolute inset-0 bg-slate-50 z-50 flex flex-col">
        <div className="bg-white border-b border-slate-100 flex items-center px-4 py-3 sticky top-0 z-10 shadow-sm">
          <button onClick={() => setActiveDeeplink(null)} className="p-2 -ml-2 text-slate-500 hover:text-slate-700 transition-colors rounded-full hover:bg-slate-50">
            <ArrowLeft size={20} />
          </button>
          <h2 className="text-base font-semibold text-slate-800 ml-2">{activeDeeplink.label}</h2>
        </div>
        <div className="flex-1 overflow-y-auto p-4 flex flex-col items-center justify-start pt-6 text-center">
          {renderDeeplinkContent()}
        </div>
      </div>
    );
  };
"""

content = content.replace(target, replacement)
with open("frontend/src/App.jsx", "w") as f:
    f.write(content)
print("Patched App.jsx")
