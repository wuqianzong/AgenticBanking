import React, { useState, useEffect, useRef } from 'react';
import { 
  Send, Bot, ChevronRight, CheckCircle2, 
  CreditCard, Wallet, Landmark, ShieldCheck, 
  FileText, UserCheck, AlertCircle, LogIn, LogOut, Lock as LockIcon,
  Loader2 // <-- ADDED for the Agentic Processing Tracker
} from 'lucide-react';

// 1. THE NODE CAPTION DICTIONARY
const NODE_CAPTIONS = {
  "memory_compaction": "Organizing your conversation context...",
  "memory_validation": "Validating saved conversation context...",
  "n00": "Securing your connection...",
  "n3": "Reviewing our chat history...",
  "n16": "Reviewing bank guidelines...",
  "n2d": "Triaging your request...",
  "n2e": "Breaking your request into topics...",
  "n14": "Preparing to search...",
  "n17_rag": "Searching Horizon Bank guides...",
  "n18": "Selecting the relevant FAQ evidence...",
  "n19": "Designing the best response experience...",
  "n8": "Double-checking the requirements...",
  "n20": "Planning the next steps...",
  "n21": "Drafting my response...",
  "n22": "Polishing the details...",
  "n24": "Running a quick final safety check..."
};

const MOCK_PROFILE = {
  userId: "USR_999",
  displayName: "Mr. Wu"
};
const SESSION_TIMEOUT_MS = 15 * 60 * 1000;
const HISTORY_PAGE_SIZE = 20;
const ACTIVE_SESSION_KEY = `horizon_active_session_${MOCK_PROFILE.userId}`;

const createSessionId = () => `sess_${crypto.randomUUID()}`;

const formatMessageDate = (timestamp) => new Intl.DateTimeFormat('en-SG', {
  year: 'numeric',
  month: 'short',
  day: 'numeric'
}).format(new Date(timestamp));

const formatMessageTime = (timestamp) => new Intl.DateTimeFormat('en-SG', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false
}).format(new Date(timestamp));

export default function BankingBotUI() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(ACTIVE_SESSION_KEY));
  const [isLoggedIn, setIsLoggedIn] = useState(() => Boolean(localStorage.getItem(ACTIVE_SESSION_KEY)));
  const [messages, setMessages] = useState([]);

  const [inputValue, setInputValue] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [isCompactingMemory, setIsCompactingMemory] = useState(false);
  const [loadingCaption, setLoadingCaption] = useState('Processing...'); // <-- NEW STATE FOR TRACKER
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [expandedProgressSteps, setExpandedProgressSteps] = useState({});

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const chatContainerRef = useRef(null);
  const sessionTimeoutRef = useRef(null);
  const isArchivingRef = useRef(false);
  const isLoadingHistoryRef = useRef(false);
  const hasMoreHistoryRef = useRef(false);
  const hasMoreCurrentSessionRef = useRef(false);
  const oldestSequenceRef = useRef(null);
  const oldestPriorRowIdRef = useRef(null);
  const shouldAutoScrollRef = useRef(true);

  const toUiMessage = (message, targetSessionId) => {
    const payload = message.ui_payload || {};
    return {
      id: `${message.session_id || targetSessionId}-${message.sequence}`,
      sender: message.role === 'user' ? 'user' : 'bot',
      text: message.content,
      type: payload.uiType || 'text',
      options: payload.options || [],
      items: payload.items || [],
      progressTitle: payload.progressTitle || '',
      stepDetail: payload.stepDetail || '',
      stepDetails: payload.stepDetails || [],
      clarificationText: payload.clarificationText || '',
      listTitle: payload.listTitle || '',
      listIntro: payload.listIntro || '',
      listSupportingText: payload.listSupportingText || '',
      listClarificationText: payload.listClarificationText || '',
      precisionSubstituted: payload.precisionSubstituted || false,
      currentStep: payload.currentStep,
      totalSteps: payload.totalSteps,
      createdAt: message.created_at
    };
  };

  const loadHistoryPage = async (targetSessionId, beforeSequence = null, replace = false) => {
    if (!targetSessionId || isLoadingHistoryRef.current) return;
    isLoadingHistoryRef.current = true;

    try {
      const query = new URLSearchParams({
        session_id: targetSessionId,
        limit: String(HISTORY_PAGE_SIZE)
      });
      if (beforeSequence !== null) query.set('before_sequence', String(beforeSequence));

      const response = await fetch(`http://localhost:8000/api/history/${MOCK_PROFILE.userId}?${query}`);
      if (!response.ok) throw new Error(`History request failed: ${response.status}`);
      const page = await response.json();
      const historyMessages = page.messages.map((message) => toUiMessage(message, targetSessionId));
      const container = chatContainerRef.current;
      const previousHeight = container?.scrollHeight ?? 0;
      const previousTop = container?.scrollTop ?? 0;
      if (!replace) shouldAutoScrollRef.current = false;

      setMessages((currentMessages) => {
        if (replace) return historyMessages;
        const knownIds = new Set(currentMessages.map((message) => message.id));
        return [...historyMessages.filter((message) => !knownIds.has(message.id)), ...currentMessages];
      });

      hasMoreHistoryRef.current = page.has_more;
      hasMoreCurrentSessionRef.current = page.has_more;
      oldestSequenceRef.current = page.next_before_sequence;
      if (replace) oldestPriorRowIdRef.current = null;
      const moreHistoryAvailable = page.has_more || page.has_prior_history;
      hasMoreHistoryRef.current = moreHistoryAvailable;
      setHasMoreHistory(moreHistoryAvailable);
      if (!replace && container) {
        requestAnimationFrame(() => {
          container.scrollTop = previousTop + (container.scrollHeight - previousHeight);
        });
      }
    } catch (error) {
      console.error("Chat-history retrieval failed:", error);
    } finally {
      isLoadingHistoryRef.current = false;
    }
  };

  const loadPriorSessionHistory = async (targetSessionId) => {
    if (!targetSessionId || isLoadingHistoryRef.current) return;
    isLoadingHistoryRef.current = true;

    try {
      const query = new URLSearchParams({
        current_session_id: targetSessionId,
        limit: String(HISTORY_PAGE_SIZE)
      });
      if (oldestPriorRowIdRef.current !== null) {
        query.set('before_row_id', String(oldestPriorRowIdRef.current));
      }

      const response = await fetch(`http://localhost:8000/api/history/${MOCK_PROFILE.userId}/previous?${query}`);
      if (!response.ok) throw new Error(`Prior-history request failed: ${response.status}`);
      const page = await response.json();
      const historyMessages = page.messages.map((message) => toUiMessage(message, targetSessionId));
      const container = chatContainerRef.current;
      const previousHeight = container?.scrollHeight ?? 0;
      const previousTop = container?.scrollTop ?? 0;
      shouldAutoScrollRef.current = false;

      setMessages((currentMessages) => {
        const knownIds = new Set(currentMessages.map((message) => message.id));
        return [...historyMessages.filter((message) => !knownIds.has(message.id)), ...currentMessages];
      });
      oldestPriorRowIdRef.current = page.next_before_row_id;
      hasMoreHistoryRef.current = page.has_more;
      setHasMoreHistory(page.has_more);
      if (container) {
        requestAnimationFrame(() => {
          container.scrollTop = previousTop + (container.scrollHeight - previousHeight);
        });
      }
    } catch (error) {
      console.error("Earlier chat-history retrieval failed:", error);
    } finally {
      isLoadingHistoryRef.current = false;
    }
  };

  const archiveActiveSession = async (reason) => {
    if (!sessionId || isArchivingRef.current) return;
    isArchivingRef.current = true;

    try {
      await fetch("http://localhost:8000/api/session/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          user_id: MOCK_PROFILE.userId,
          reason
        })
      });
    } catch (error) {
      console.error("Session archive failed:", error);
    } finally {
      isArchivingRef.current = false;
    }
  };

  const scheduleSessionTimeout = () => {
    window.clearTimeout(sessionTimeoutRef.current);
    sessionTimeoutRef.current = window.setTimeout(async () => {
      await archiveActiveSession("timeout");
      setIsLoggedIn(false);
      setMessages([]);
      setSessionId(null);
      oldestSequenceRef.current = null;
      oldestPriorRowIdRef.current = null;
      hasMoreHistoryRef.current = false;
      setHasMoreHistory(false);
      localStorage.removeItem(ACTIVE_SESSION_KEY);
    }, SESSION_TIMEOUT_MS);
  };

  const handleLoginToggle = async () => {
    if (isLoggedIn) {
      window.clearTimeout(sessionTimeoutRef.current);
      await archiveActiveSession("logout");
      setIsLoggedIn(false);
      setMessages([]);
      setSessionId(null);
      oldestSequenceRef.current = null;
      oldestPriorRowIdRef.current = null;
      hasMoreHistoryRef.current = false;
      setHasMoreHistory(false);
      localStorage.removeItem(ACTIVE_SESSION_KEY);
      return;
    }

    const newSessionId = createSessionId();
    localStorage.setItem(ACTIVE_SESSION_KEY, newSessionId);
    setSessionId(newSessionId);
    setIsLoggedIn(true);
    shouldAutoScrollRef.current = true;
    oldestSequenceRef.current = null;
    oldestPriorRowIdRef.current = null;
    hasMoreHistoryRef.current = false;
    setHasMoreHistory(false);
    setMessages([]);
    await handleSend("**INIT**", { isInitialization: true, sessionIdOverride: newSessionId });
    scheduleSessionTimeout();
  };

  const scrollToBottom = () => {
    // Scroll the chat pane itself. `scrollIntoView` can select the outer page
    // instead of this nested, scrollable phone viewport.
    const container = chatContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
      return;
    }
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  };

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;

    // Wait for React to lay out a full answer/progress card before reading the
    // container's height.
    const frameId = requestAnimationFrame(scrollToBottom);
    return () => cancelAnimationFrame(frameId);
  }, [messages, isTyping]);

  useEffect(() => {
    if (!isTyping && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isTyping]);

  useEffect(() => {
    if (sessionId) {
      void loadHistoryPage(sessionId, null, true);
      scheduleSessionTimeout();
    }

    return () => window.clearTimeout(sessionTimeoutRef.current);
  }, []);

  // ⚡ ACTUAL END-TO-END NETWORK CALL TO LANGGRAPH/FASTAPI
  const handleSend = async (text, { isInitialization = false, sessionIdOverride = null } = {}) => {
    if (!text.trim()) return;
    const targetSessionId = sessionIdOverride || sessionId;
    if (!targetSessionId) return;

    if (!isInitialization) {
      shouldAutoScrollRef.current = true;
      const newUserMsg = {
        id: Date.now(),
        sender: 'user',
        text,
        type: 'text',
        createdAt: new Date().toISOString()
      };
      setMessages(prev => [...prev, newUserMsg]);
      setInputValue('');
    }
    setIsTyping(true);
    setLoadingCaption('Initializing agentic workflow...'); // Reset caption

    try {
      const response = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Note: Backend must return application/x-ndjson stream
        },
        body: JSON.stringify({
          session_id: targetSessionId,
          message: text,
          user_id: MOCK_PROFILE.userId
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // 2. THE FETCH & STREAM LOGIC (With X-Ray Debugging)
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let finalBotData = null;
      let buffer = "";

      console.log("🚀 [STREAM START] Connected to backend, waiting for chunks...");

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          console.log("🏁 [STREAM DONE] Connection closed by backend.");
          break;
        }

        // Decode the raw network packet
        const chunkString = decoder.decode(value, { stream: true });
        // Use JSON.stringify so we can actually see the hidden \n characters in the console
        console.log("📦 [RAW CHUNK ARRIVED]:", JSON.stringify(chunkString));

        buffer += chunkString;

        const lines = buffer.split('\n');
        buffer = lines.pop(); // Hold back the incomplete line

        console.log("✂️ [LINES EXTRACTED]:", lines);
        console.log("💧 [BUFFER HELD BACK]:", JSON.stringify(buffer));

        for (const line of lines) {
          if (line.trim() === '') {
            continue;
          }

          try {
            console.log("🔍 [ATTEMPTING JSON PARSE ON]:", JSON.stringify(line));
            const parsedData = JSON.parse(line);
            console.log("✅ [PARSE SUCCESS]:", parsedData);

            if (parsedData.type === 'memory_compaction') {
              const isStarting = parsedData.status === 'started';
              setIsCompactingMemory(isStarting);
              setLoadingCaption(
                isStarting
                  ? 'Organizing your conversation context...'
                  : 'Conversation context organized.'
              );
              continue;
            }

            // Check if the payload is a node transition update
            if (parsedData.node) {
              // Use the dictionary if it exists, otherwise show a generic processing message
              const caption = NODE_CAPTIONS[parsedData.node] || `Processing step: ${parsedData.node}...`;
              console.log("🔄 [UPDATING UI STATE TO]:", caption);
              setLoadingCaption(caption);
            }

            // Check if the payload contains the final response
            if (parsedData.text || parsedData.uiType) {
              finalBotData = parsedData;
            }
          } catch (err) {
            console.error("❌ [PARSE FAILED]:", err.message, "|| ON STRING:", JSON.stringify(line));
          }
        }
      }

      // 4. Map the final backend data to our React UI state
      if (finalBotData) {
        if (finalBotData.uiType === 'history_sync') {
          setMessages(finalBotData.items.map((message, index) => ({
            id: `history-${index}`,
            sender: message.role === 'user' ? 'user' : 'bot',
            text: message.content,
            type: 'text',
            createdAt: new Date().toISOString()
          })));
        } else if (finalBotData.text) {
          // A completed assistant reply should always take the customer to the
          // current answer, even if they previously loaded older history.
          shouldAutoScrollRef.current = true;
          const botResponse = {
            id: Date.now() + 1,
            sender: 'bot',
            text: finalBotData.text,
            type: finalBotData.uiType || 'text',
            options: finalBotData.options || [],
            items: finalBotData.items || [],
            progressTitle: finalBotData.progressTitle || '',
            stepDetail: finalBotData.stepDetail || '',
            stepDetails: finalBotData.stepDetails || [],
            clarificationText: finalBotData.clarificationText || '',
            listTitle: finalBotData.listTitle || '',
            listIntro: finalBotData.listIntro || '',
            listSupportingText: finalBotData.listSupportingText || '',
            listClarificationText: finalBotData.listClarificationText || '',
            precisionSubstituted: finalBotData.precisionSubstituted || false,
            currentStep: finalBotData.currentStep,
            totalSteps: finalBotData.totalSteps,
            createdAt: new Date().toISOString()
          };
          if (botResponse.type === 'stepper') {
            setExpandedProgressSteps(prev => ({ ...prev, [botResponse.id]: 1 }));
          }
          setMessages(prev => [...prev, botResponse]);
        }

        // Turn 0 is a plain greeting, so it is safe to replace it with the
        // persisted version here. This also obtains the prior-history metadata
        // needed to show the "Load earlier messages" control on a fresh login.
        if (isInitialization) {
          await loadHistoryPage(targetSessionId, null, true);
        }
      }

    } catch (error) {
      console.error("Backend Connection Error:", error);
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        sender: 'bot',
        text: "⚠️ Connection Error: Cannot reach the LangGraph backend. Please ensure uvicorn is running on port 8000.",
        type: 'escalation'
      }]);
    } finally {
      setIsCompactingMemory(false);
      setIsTyping(false);
      scheduleSessionTimeout();
    }
  };

  const handleHistoryScroll = (event) => {
    if (
      event.currentTarget.scrollTop <= 24
      && hasMoreHistoryRef.current
      && oldestSequenceRef.current !== null
    ) {
      if (hasMoreCurrentSessionRef.current) {
        void loadHistoryPage(sessionId, oldestSequenceRef.current);
      } else {
        void loadPriorSessionHistory(sessionId);
      }
    }
  };

  // --- UI WIDGET RENDERERS ---

  const renderCarousel = (options) => (
    <div className="flex overflow-x-auto snap-x snap-mandatory gap-3 pb-2 pt-1 -mx-2 px-2 hide-scrollbar w-[300px] md:w-[400px]">
      {options?.map((opt) => (
        <div 
          key={opt.id}
          onClick={() => handleSend(opt.title)}
          className="flex-shrink-0 w-48 bg-white border border-slate-200 rounded-xl p-4 shadow-sm cursor-pointer hover:border-blue-500 hover:shadow-md transition-all snap-center group"
        >
          <div className="bg-blue-50 text-blue-600 w-10 h-10 rounded-full flex items-center justify-center mb-3 group-hover:bg-blue-600 group-hover:text-white transition-colors">
            {opt.icon === 'wallet' && <Wallet size={20} />}
            {opt.icon === 'card' && <CreditCard size={20} />}
            {opt.icon === 'landmark' && <Landmark size={20} />}
          </div>
          <h4 className="font-semibold text-slate-800 text-sm mb-1">{opt.title}</h4>
          <p className="text-xs text-slate-500">{opt.desc}</p>
        </div>
      ))}
    </div>
  );

  const renderChips = (options) => (
    <div className="flex flex-wrap gap-2 mt-2">
      {options?.map((opt, idx) => (
        <button
          key={idx}
          onClick={() => handleSend(opt)}
          disabled={isTyping || isCompactingMemory}
          className="bg-white border border-blue-200 text-blue-700 hover:bg-blue-50 px-4 py-2 rounded-full text-sm font-medium transition-colors shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          {opt}
        </button>
      ))}
    </div>
  );

  const renderPrecisionBadge = () => (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700 normal-case tracking-normal">
      <CheckCircle2 size={10} />
      Number accurate
    </span>
  );

  const renderStepper = (items, progressTitle, totalSteps, options, stepDetail, stepDetails, expandedStepNumber, onToggleStep) => (
    <div className="mt-3 w-full max-w-sm overflow-hidden rounded-xl border border-blue-100 bg-white shadow-sm">
      <div className="border-b border-blue-100 bg-blue-50 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-blue-900">
            <FileText size={16} className="text-blue-600" />
            <span className="text-sm font-semibold">{progressTitle || 'Horizon Bank guided process'}</span>
          </div>
          <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-semibold text-blue-700 shadow-sm">
            {expandedStepNumber}/{totalSteps}
          </span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-blue-100">
          <div
            className="h-full rounded-full bg-blue-600 transition-all duration-500"
            style={{ width: `${totalSteps ? (expandedStepNumber / totalSteps) * 100 : 0}%` }}
          />
        </div>
      </div>
      <ol className="space-y-0 px-3 py-1.5">
        {items?.map((item, index) => {
          const isCompleted = item.status === 'completed';
          const isExpanded = expandedStepNumber === item.number;
          const detail = stepDetails?.[index] || (item.number === 1 ? stepDetail : '');
          return (
            <li key={`${item.number}-${item.label}`} className="flex gap-2 py-2">
              <div className="flex flex-col items-center">
                <div className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                  isCompleted
                    ? 'bg-emerald-500 text-white'
                    : isExpanded
                      ? 'bg-blue-600 text-white ring-4 ring-blue-100'
                      : 'border border-slate-200 bg-white text-slate-400'
                }`}>
                  {isCompleted ? <CheckCircle2 size={12} /> : item.number}
                </div>
                {index < items.length - 1 && <div className={`mt-1 h-3 w-px ${isCompleted ? 'bg-emerald-300' : 'bg-slate-200'}`} />}
              </div>
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => onToggleStep(item.number)}
                  className="flex w-full items-start justify-between gap-2 text-left"
                  aria-expanded={isExpanded}
                >
                  <span className={`text-sm leading-snug ${isExpanded ? 'font-semibold text-slate-800' : 'text-slate-500'}`}>
                    {item.label}
                  </span>
                  <ChevronRight size={16} className={`mt-0.5 shrink-0 text-slate-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
                </button>
                {isExpanded && detail && (
                  <p className="mt-1.5 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-2 text-sm leading-relaxed text-slate-700 whitespace-pre-wrap">
                    {detail}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {options?.length > 0 && <div className="border-t border-slate-100 px-4 pb-3">{renderChips(options)}</div>}
    </div>
  );

  const renderClarificationCard = (text) => (
    <div className="mt-3 flex max-w-sm items-start gap-2.5 rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 shadow-sm">
      <AlertCircle size={16} className="mt-1 shrink-0 text-blue-600" aria-hidden="true" />
      <span className="whitespace-pre-wrap">{text}</span>
    </div>
  );

  const renderChecklist = (items, title, options = []) => (
    <div className="bg-white rounded-xl border border-slate-200 p-4 mt-2 shadow-sm w-full max-w-sm">
      <div className="flex items-center gap-2 mb-3 pb-3 border-b border-slate-100">
        <FileText size={18} className="text-blue-600" />
        <h4 className="font-semibold text-slate-800 text-sm">{title || 'Key information'}</h4>
      </div>
      <ul className="space-y-3">
        {items?.map((item, idx) => (
          <li key={idx} className="flex items-start gap-3">
            <CheckCircle2 size={18} className="text-emerald-500 shrink-0 mt-0.5" />
            <span className="text-sm text-slate-600 leading-snug">{item}</span>
          </li>
        ))}
      </ul>
      {options.length > 0 && <div className="mt-4 border-t border-slate-100 pt-3">{renderChips(options)}</div>}
    </div>
  );

  const renderEscalation = () => (
    <div className="bg-red-50 rounded-xl border border-red-200 p-4 mt-2 shadow-sm w-full max-w-sm">
      <div className="flex items-center gap-2 mb-2 text-red-600">
        <AlertCircle size={18} />
        <h4 className="font-semibold text-sm">Guardrail Triggered</h4>
      </div>
      <p className="text-xs text-red-700 mb-3 leading-relaxed">
        To comply with MAS regulations, I cannot provide financial advice or execute unauthorized transactions. I am preserving your session context and transferring you to a human specialist.
      </p>
      <button className="w-full bg-slate-800 hover:bg-slate-900 text-white font-medium py-2 rounded-lg text-sm transition-colors flex items-center justify-center gap-2 shadow-sm">
        <UserCheck size={16} /> Connect to Live Agent
      </button>
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4 font-sans">
      
      {/* Mobile Device Mockup */}
      <div className="w-full max-w-md bg-white rounded-[2.5rem] shadow-2xl overflow-hidden flex flex-col h-[85vh] max-h-[850px] border-[6px] border-slate-800 relative">
        
        {/* Dynamic User Identity Header */}
        <div className="bg-white border-b border-slate-100 p-4 pb-3 flex flex-col gap-3 z-10 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="bg-blue-600 w-11 h-11 rounded-full flex items-center justify-center shadow-md">
                <Bot className="text-white" size={24} />
              </div>
              <div>
                <h1 className="font-bold text-slate-800 text-lg leading-tight">Horizon Assistant</h1>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  <span className="text-[11px] text-slate-500 font-medium uppercase tracking-wide">Secure Session</span>
                </div>
              </div>
            </div>
            {/* Login Toggle */}
            <button onClick={handleLoginToggle} className="p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors" title="Toggle User State">
              {isLoggedIn ? <LogOut size={18}/> : <LogIn size={18}/>}
            </button>
          </div>

          {/* User Status Bar */}
          <div className={`flex items-center justify-between px-3 py-2 rounded-lg border ${isLoggedIn ? 'bg-blue-50/50 border-blue-100' : 'bg-slate-50 border-slate-100'}`}>
             <div className="flex items-center gap-2">
               {isLoggedIn ? (
                 <>
                   <UserCheck size={14} className="text-blue-600"/>
                   <span className="text-xs font-semibold text-blue-900">{MOCK_PROFILE.displayName}</span>
                 </>
               ) : (
                 <>
                   <AlertCircle size={14} className="text-slate-500"/>
                   <span className="text-xs font-medium text-slate-600">Guest User</span>
                 </>
               )}
             </div>
             {isLoggedIn && <span className="text-[10px] font-mono text-slate-500 bg-white px-2 py-0.5 rounded border border-slate-200">ACC: •••• 1234</span>}
          </div>
        </div>

        {/* Chat Conversation Area */}
        <div
          ref={chatContainerRef}
          onScroll={handleHistoryScroll}
          className="flex-1 overflow-y-auto p-4 space-y-5 bg-slate-50/50"
        >
          
          <div className="bg-white/80 backdrop-blur-sm text-slate-600 text-[11px] p-3 rounded-xl border border-slate-200 flex items-start gap-2 mb-4 shadow-sm">
             <ShieldCheck size={16} className="text-blue-600 shrink-0 mt-0.5" />
             <p><b>MVP Mode:</b> No static menus. Use natural language to navigate. Try asking <i>"How do I open an account?"</i></p>
          </div>

          {isLoggedIn && hasMoreHistory && (
            <div className="flex justify-center pb-1">
              <button
                onClick={() => {
                  if (hasMoreCurrentSessionRef.current) {
                    void loadHistoryPage(sessionId, oldestSequenceRef.current);
                  } else {
                    void loadPriorSessionHistory(sessionId);
                  }
                }}
                disabled={isLoadingHistoryRef.current}
                className="text-xs font-medium text-blue-700 hover:text-blue-900 disabled:opacity-50"
              >
                Load earlier messages
              </button>
            </div>
          )}

          {messages.map((msg, index) => {
            const timestamp = msg.createdAt || new Date().toISOString();
            const dateLabel = formatMessageDate(timestamp);
            const previousDateLabel = index > 0
              ? formatMessageDate(messages[index - 1].createdAt || timestamp)
              : null;

            return (
              <React.Fragment key={msg.id}>
                {dateLabel !== previousDateLabel && (
                  <div className="flex items-center gap-3 py-1">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{dateLabel}</span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>
                )}
                <div className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className="max-w-[88%]">
                    {msg.sender === 'bot' && (
                      <div className="flex items-end gap-2 mb-1.5 ml-1">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Horizon</span>
                        {msg.precisionSubstituted && renderPrecisionBadge()}
                      </div>
                    )}

                    {msg.type !== 'stepper' && msg.type !== 'checklist' && (
                      <>
                        <div className={`p-3.5 text-[15px] leading-relaxed shadow-sm whitespace-pre-wrap
                          ${msg.sender === 'user'
                            ? 'bg-blue-600 text-white rounded-2xl rounded-tr-sm'
                            : 'bg-white border border-slate-200 text-slate-700 rounded-2xl rounded-tl-sm'
                          }`}
                        >
                          {msg.text}
                        </div>
                        <div className={`mt-1 text-[10px] tabular-nums text-slate-400 ${msg.sender === 'user' ? 'text-right mr-1' : 'ml-1'}`}>
                          {formatMessageTime(timestamp)}
                        </div>
                      </>
                    )}

                    {/* Dynamic UI Components */}
                    {msg.type === 'carousel' && renderCarousel(msg.options)}
                    {msg.type === 'chips' && renderChips(msg.options)}
                    {msg.type === 'stepper' && renderStepper(
                      msg.items,
                      msg.progressTitle,
                      msg.totalSteps,
                      msg.options,
                      msg.stepDetail,
                      msg.stepDetails,
                      expandedProgressSteps[msg.id] ?? 1,
                      (stepNumber) => setExpandedProgressSteps(prev => ({
                        ...prev,
                        [msg.id]: stepNumber
                      }))
                    )}
                    {msg.type === 'stepper' && msg.clarificationText && (
                      renderClarificationCard(msg.clarificationText)
                    )}
                    {msg.type === 'checklist' && msg.listIntro && (
                      <div className="mt-2 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {msg.listIntro}
                      </div>
                    )}
                    {msg.type === 'checklist' && renderChecklist(msg.items, msg.listTitle, msg.options)}
                    {msg.type === 'checklist' && msg.listSupportingText && (
                      <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {msg.listSupportingText}
                      </div>
                    )}
                    {msg.type === 'checklist' && msg.listClarificationText && renderClarificationCard(msg.listClarificationText)}
                    {(msg.type === 'stepper' || msg.type === 'checklist') && (
                      <div className="mt-1 ml-1 text-[10px] tabular-nums text-slate-400">
                        {formatMessageTime(timestamp)}
                      </div>
                    )}
                    {msg.type === 'escalation' && renderEscalation()}
                  </div>
                </div>
              </React.Fragment>
            );
          })}
          
          {/* 3. UI RENDERING REQUIREMENT: Agentic Processing Tracker */}
          {isTyping && (
            <div className="flex justify-start">
              <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3.5 flex items-center gap-3 shadow-sm max-w-[88%]">
                <Loader2 size={16} className="text-blue-600 animate-spin shrink-0" />
                <span className="text-[13px] text-slate-600 font-medium tracking-wide">
                  {loadingCaption}
                </span>
              </div>
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>

        {/* Text Input Area */}
        <div className="bg-white border-t border-slate-200 p-3 z-10 shadow-[0_-4px_10px_rgba(0,0,0,0.02)]">
          <div className="relative flex items-center">
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend(inputValue)}
              placeholder="Type your message..."
              className="w-full bg-slate-100 text-slate-800 text-[15px] rounded-full pl-5 pr-12 py-3.5 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all placeholder:text-slate-400"
              disabled={isTyping || !isLoggedIn}
            />
            <button
              onClick={() => handleSend(inputValue)}
              disabled={!inputValue.trim() || isTyping || !isLoggedIn}
              className="absolute right-1.5 bg-blue-600 text-white w-10 h-10 rounded-full flex items-center justify-center disabled:opacity-50 disabled:bg-slate-300 hover:bg-blue-700 transition-colors shadow-sm"
            >
              <Send size={18} className="ml-0.5" />
            </button>
          </div>
          <div className="flex items-center justify-center gap-1 mt-3 mb-1">
             <LockIcon size={10} className="text-slate-400"/>
             <span className="text-[10px] text-slate-400 font-medium tracking-wide uppercase">Secured by 256-bit encryption (Out of scope)</span>
          </div>
        </div>
      </div>
      
      {/* Hide Scrollbars CSS */}
      <style dangerouslySetInnerHTML={{__html: `
        .hide-scrollbar::-webkit-scrollbar { display: none; }
        .hide-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      `}} />
    </div>
  );
}
