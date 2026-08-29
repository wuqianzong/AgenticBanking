  const renderDeeplinkContent = () => {
    switch (activeDeeplink.destinationId) {
      case 'card-management':
        return (
          <div className="w-full text-left space-y-4">
            <div className="bg-gradient-to-r from-slate-800 to-slate-900 rounded-xl p-5 text-white shadow-lg relative overflow-hidden">
              <div className="absolute -right-4 -top-4 w-24 h-24 bg-white opacity-5 rounded-full blur-xl"></div>
              <div className="flex justify-between items-start mb-6 relative z-10">
                <div>
                  <div className="text-xs text-slate-300 mb-1">Horizon Rewards Visa</div>
                  <div className="font-mono text-lg tracking-widest">**** 4912</div>
                </div>
                <CreditCard size={24} className="text-slate-300" />
              </div>
              <div className="flex justify-between items-end relative z-10">
                <div>
                  <div className="text-[10px] text-slate-400 uppercase tracking-wider">Cardholder</div>
                  <div className="text-sm font-medium">{preferences.nickname || 'Customer'}</div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] text-slate-400 uppercase tracking-wider">Status</div>
                  <div className="text-sm font-medium text-emerald-400 flex items-center gap-1">
                    <CheckCircle2 size={14} /> Active
                  </div>
                </div>
              </div>
            </div>
            
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <span className="text-sm font-medium text-slate-700">Lock Card temporarily</span>
                <div className="w-10 h-6 bg-slate-200 rounded-full relative cursor-pointer">
                  <div className="w-4 h-4 bg-white rounded-full absolute left-1 top-1 shadow-sm"></div>
                </div>
              </div>
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center">
                <span className="text-sm font-medium text-slate-700">Online Transactions</span>
                <div className="w-10 h-6 bg-blue-500 rounded-full relative cursor-pointer">
                  <div className="w-4 h-4 bg-white rounded-full absolute right-1 top-1 shadow-sm"></div>
                </div>
              </div>
              <div className="px-4 py-3 flex justify-between items-center text-red-600 hover:bg-red-50 cursor-pointer transition-colors">
                <span className="text-sm font-medium">Report Lost or Stolen</span>
                <ChevronRight size={16} />
              </div>
            </div>
          </div>
        );
      
      case 'account-statements':
        return (
          <div className="w-full text-left space-y-3">
            <div className="flex space-x-2 mb-4">
              <button className="flex-1 bg-blue-50 text-blue-600 text-sm font-medium py-2 rounded-lg border border-blue-100">2026</button>
              <button className="flex-1 bg-white text-slate-600 text-sm font-medium py-2 rounded-lg border border-slate-200">2025</button>
              <button className="flex-1 bg-white text-slate-600 text-sm font-medium py-2 rounded-lg border border-slate-200">Older</button>
            </div>
            
            {['July', 'June', 'May', 'April'].map((month, i) => (
              <div key={month} className="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between shadow-sm cursor-pointer hover:border-blue-300 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-red-50 rounded-lg flex items-center justify-center text-red-500">
                    <FileText size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-slate-800">{month} 2026 Statement</div>
                    <div className="text-xs text-slate-500">PDF • {(2.1 + i * 0.3).toFixed(1)} MB</div>
                  </div>
                </div>
                <div className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center text-slate-400">
                  <ArrowRight size={16} />
                </div>
              </div>
            ))}
          </div>
        );
        
      case 'profile-personal-details':
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
        
      case 'security-settings':
        return (
          <div className="w-full text-left space-y-4">
            <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden mb-6">
              <div className="p-4 border-b border-slate-100 flex items-center gap-3">
                <div className="w-10 h-10 bg-emerald-50 rounded-full flex items-center justify-center text-emerald-500">
                  <ShieldCheck size={20} />
                </div>
                <div>
                  <div className="text-sm font-medium text-slate-800">Security Checkup</div>
                  <div className="text-xs text-slate-500">Your account is well protected</div>
                </div>
              </div>
            </div>
            
            <h4 className="text-sm font-medium text-slate-800 px-1">Authentication</h4>
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center">
                <span className="text-sm font-medium text-slate-700">Face ID Login</span>
                <div className="w-10 h-6 bg-blue-500 rounded-full relative cursor-pointer">
                  <div className="w-4 h-4 bg-white rounded-full absolute right-1 top-1 shadow-sm"></div>
                </div>
              </div>
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center cursor-pointer hover:bg-slate-50">
                <span className="text-sm font-medium text-slate-700">Change PIN</span>
                <ChevronRight size={16} className="text-slate-400" />
              </div>
              <div className="px-4 py-3 flex justify-between items-center cursor-pointer hover:bg-slate-50">
                <span className="text-sm font-medium text-slate-700">Two-Factor Authentication</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">Enabled</span>
                  <ChevronRight size={16} className="text-slate-400" />
                </div>
              </div>
            </div>
          </div>
        );
        
      case 'pay-credit-card-bill':
        return (
          <div className="w-full text-left space-y-4">
            <div className="bg-blue-600 rounded-xl p-5 text-white shadow-md">
              <div className="text-blue-100 text-sm mb-1">Current Balance</div>
              <div className="text-3xl font-bold mb-4">$1,250.00</div>
              <div className="flex justify-between items-center text-xs bg-blue-700/50 p-2 rounded-lg">
                <span>Minimum Payment: $50.00</span>
                <span>Due: Aug 28, 2026</span>
              </div>
            </div>
            
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Payment Amount</label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <span className="text-slate-500 font-medium">$</span>
                  </div>
                  <input type="text" className="block w-full pl-8 pr-3 py-2 border border-slate-200 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-slate-800 font-medium" defaultValue="1250.00" />
                </div>
              </div>
              
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">Pay From</label>
                <div className="flex justify-between items-center p-3 border border-slate-200 rounded-lg cursor-pointer hover:bg-slate-50">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 bg-teal-50 text-teal-600 rounded-full flex items-center justify-center">
                      <Wallet size={16} />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-slate-800">Everyday Savings</div>
                      <div className="text-xs text-slate-500">Available: $8,420.50</div>
                    </div>
                  </div>
                  <ChevronRight size={16} className="text-slate-400" />
                </div>
              </div>
            </div>
            
            <button className="w-full bg-blue-600 text-white font-medium py-3.5 rounded-xl shadow-sm hover:bg-blue-700 transition-colors mt-2 flex justify-center items-center gap-2">
              Pay Now
            </button>
          </div>
        );
        
      default:
        return (
          <div className="w-full max-w-xs bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-left mt-6">
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
        );
    }
  };
