with open("frontend/src/App.jsx", "r") as f:
    content = f.read()

target = """            stepDetails: finalBotData.stepDetails || [],
            clarificationText: finalBotData.clarificationText || '',
            listTitle: finalBotData.listTitle || '',
            listIntro: finalBotData.listIntro || '',
            extendedText: finalBotData.extendedText || '',
            listClarificationText: finalBotData.listClarificationText || '',
            precisionSubstituted: finalBotData.precisionSubstituted || false,
            currentStep: finalBotData.currentStep,
            totalSteps: finalBotData.totalSteps,
            serviceRequestActions: finalBotData.serviceRequestActions || []"""

replacement = """            stepDetails: finalBotData.stepDetails || [],
            clarificationText: finalBotData.clarificationText || '',
            listTitle: finalBotData.listTitle || '',
            listIntro: finalBotData.listIntro || '',
            extendedText: finalBotData.extendedText || '',
            listClarificationText: finalBotData.listClarificationText || '',
            precisionSubstituted: finalBotData.precisionSubstituted || false,
            currentStep: finalBotData.currentStep,
            totalSteps: finalBotData.totalSteps,
            serviceRequestActions: finalBotData.serviceRequestActions || [],
            navigationAction: finalBotData.navigationAction || null"""

content = content.replace(target, replacement)

target2 = """                      <div className={`mt-1 text-[10px] tabular-nums text-slate-400 ${msg.sender === 'user' ? 'text-right mr-1' : 'ml-1'}`}>
                        {formatMessageTime(timestamp)}
                      </div>
                    </>
                  )}"""

replacement2 = """                      {msg.navigationAction && (
                        <div className="mt-3 w-full flex justify-end">
                          <button
                            onClick={() => handleNavigationAction(msg.navigationAction)}
                            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-lg text-sm font-medium transition-colors"
                          >
                            {msg.navigationAction.label}
                            <ArrowRight size={14} />
                          </button>
                        </div>
                      )}
                      <div className={`mt-1 text-[10px] tabular-nums text-slate-400 ${msg.sender === 'user' ? 'text-right mr-1' : 'ml-1'}`}>
                        {formatMessageTime(timestamp)}
                      </div>
                    </>
                  )}"""

content = content.replace(target2, replacement2)

with open("frontend/src/App.jsx", "w") as f:
    f.write(content)
print("Updated App.jsx with navigationAction rendering")
