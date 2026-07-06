import React, { useState } from 'react';
import { UserCheck, ChevronRight } from 'lucide-react';

export default function CaseSummaryWidget({ details }) {
  const [isChecked, setIsChecked] = useState(false);

  return (
    <div className="bg-slate-50 rounded-xl border border-slate-200 p-4 mt-2 shadow-sm w-full max-w-sm">
      <div className="flex items-center gap-2 mb-3 pb-3 border-b border-slate-200">
        <UserCheck size={18} className="text-slate-600" />
        <h4 className="font-semibold text-slate-800 text-sm">Mobile-Side Case Summary</h4>
      </div>
      <div className="space-y-2 mb-4 bg-white p-3 rounded-lg border border-slate-100 shadow-sm">
        {Object.entries(details).map(([k, v], idx) => (
          <div key={idx} className="flex justify-between items-center">
            <span className="text-xs font-medium text-slate-500 uppercase">{k}</span>
            <span className="text-xs font-semibold text-slate-800 text-right w-2/3">{v}</span>
          </div>
        ))}
      </div>
      <label className="flex items-start gap-2 mb-4 cursor-pointer">
        <input 
          type="checkbox" 
          className="mt-0.5 w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          checked={isChecked}
          onChange={(e) => setIsChecked(e.target.checked)}
        />
        <span className="text-xs text-slate-600 leading-tight">
          I confirm the case summary details above are accurate for agent hand-off.
        </span>
      </label>
      <button 
        disabled={!isChecked}
        className={`w-full font-medium py-2.5 rounded-lg text-sm transition-all flex items-center justify-center gap-2 shadow-sm ${
          isChecked ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
        }`}
      >
        Talk to Agent <ChevronRight size={16} />
      </button>
    </div>
  );
}