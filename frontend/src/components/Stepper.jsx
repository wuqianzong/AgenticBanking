import React from 'react';
import { CheckCircle2, ArrowRight } from 'lucide-react';

export default function StepperWidget({ currentStep, setStepperStep, triggerNextIntent }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 mt-2 shadow-sm w-full max-w-sm">
      <div className="relative">
        <div className="flex gap-4 mb-6 relative">
          <div className="flex flex-col items-center">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold z-10 ${currentStep >= 1 ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-400'}`}>1</div>
            <div className={`w-0.5 h-full absolute top-8 left-4 -ml-[1px] ${currentStep >= 2 ? 'bg-blue-600' : 'bg-slate-200'}`}></div>
          </div>
          <div className="pt-1 pb-4">
            <h4 className="font-semibold text-slate-800 text-sm">Age Verification</h4>
            <p className="text-xs text-slate-500 mt-1">You must be at least 18 years old to open a primary account.</p>
            {currentStep === 1 && (
              <button onClick={() => setStepperStep(2)} className="mt-3 bg-blue-50 text-blue-700 px-4 py-1.5 rounded-md text-xs font-semibold hover:bg-blue-100 transition-colors">Confirm & Next</button>
            )}
          </div>
        </div>
        <div className="flex gap-4 mb-6 relative">
          <div className="flex flex-col items-center">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold z-10 ${currentStep >= 2 ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-400'}`}>2</div>
            <div className={`w-0.5 h-full absolute top-8 left-4 -ml-[1px] ${currentStep >= 3 ? 'bg-blue-600' : 'bg-slate-200'}`}></div>
          </div>
          <div className="pt-1 pb-4">
            <h4 className={`font-semibold text-sm ${currentStep >= 2 ? 'text-slate-800' : 'text-slate-400'}`}>Residency Status</h4>
            <p className={`text-xs mt-1 ${currentStep >= 2 ? 'text-slate-500' : 'text-slate-400'}`}>Singapore Citizen, PR, or valid Employment Pass holder.</p>
            {currentStep === 2 && (
              <button onClick={() => setStepperStep(3)} className="mt-3 bg-blue-50 text-blue-700 px-4 py-1.5 rounded-md text-xs font-semibold hover:bg-blue-100 transition-colors">Confirm & Next</button>
            )}
          </div>
        </div>
        <div className="flex gap-4 relative">
          <div className="flex flex-col items-center">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold z-10 ${currentStep >= 3 ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-400'}`}>
               {currentStep >= 3 ? <CheckCircle2 size={16} /> : '3'}
            </div>
          </div>
          <div className="pt-1">
            <h4 className={`font-semibold text-sm ${currentStep >= 3 ? 'text-slate-800' : 'text-slate-400'}`}>Eligibility Confirmed</h4>
            <p className={`text-xs mt-1 ${currentStep >= 3 ? 'text-slate-500' : 'text-slate-400'}`}>You meet all criteria to proceed.</p>
            {currentStep === 3 && (
              <button onClick={() => triggerNextIntent("What documents do I need?")} className="mt-3 bg-blue-600 text-white px-4 py-2 rounded-md text-xs font-semibold hover:bg-blue-700 transition-colors w-full flex justify-center items-center gap-2">View Required Documents <ArrowRight size={14}/></button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}