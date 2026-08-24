import React, { useState, useEffect, useRef } from 'react';
import { t } from './translations';
import {
  Send, Bot, ChevronRight, CheckCircle2,
  CreditCard, Wallet, Landmark, ShieldCheck,
  FileText, UserCheck, AlertCircle, LogIn, LogOut, Lock as LockIcon,
  Loader2, Settings, ClipboardList, Clock, XCircle // <-- ADDED for the Agentic Processing Tracker
} from 'lucide-react';

// 1. THE NODE CAPTION DICTIONARY
const NODE_CAPTIONS = {
  "memory_compaction": "caption.memory_compaction",
  "memory_validation": "caption.memory_validation",
  "n00": "caption.n00",
  "n3": "caption.n3",
  "n16": "caption.n16",
  "n2d": "caption.n2d",
  "n2e": "caption.n2e",
  "n14": "caption.n14",
  "n17_rag": "caption.n17_rag",
  "n18": "caption.n18",
  "n19": "caption.n19",
  "n8": "caption.n8",
  "n20": "caption.n20",
  "n21_compliance": "caption.n21_compliance",
  "n21_groundedness": "caption.n21_groundedness",
  "n21": "caption.n21",
  "n22": "caption.n22",
  "n24": "caption.n24"
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
  const [loadingCaption, setLoadingCaption] = useState(t("English", "caption.processing")); // <-- NEW STATE FOR TRACKER
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [expandedProgressSteps, setExpandedProgressSteps] = useState({});
  const [showPreferences, setShowPreferences] = useState(false);
  const [preferences, setPreferences] = useState({
    nickname: "",
    language: "English",
    tone: "professional",
    emoji: "enabled",
    financial_literacy: "intermediate"
  });

  // --- WP-7: Service Request (form-gated, risk-tiered) state ---
  // activeServiceRequest holds the open form's schema + in-progress answers.
  // activeCredentialCheck holds only the special_id awaiting the mocked
  // "Bank Centralized Credential Check" gate. Only one can be open at a
  // time, and opening either blocks the main chat input (a UX choice for
  // focus, not a correctness mechanism -- see SERVICE_REQUEST_DESIGN_v1.0.md).
  const [activeServiceRequest, setActiveServiceRequest] = useState(null);
  const [activeCredentialCheck, setActiveCredentialCheck] = useState(null);
  const [serviceRequestNotice, setServiceRequestNotice] = useState('');
  const [isSubmittingServiceRequest, setIsSubmittingServiceRequest] = useState(false);
  // The to-do list: every service request the customer has ever made,
  // scoped by user_id so it persists across sessions -- pending ones are
  // reopenable, completed/expired ones are shown for reference only.
  const [serviceRequestList, setServiceRequestList] = useState([]);
  const [showServiceRequestList, setShowServiceRequestList] = useState(false);
  const [serviceRequestPage, setServiceRequestPage] = useState(1);
  const [serviceRequestTab, setServiceRequestTab] = useState('pending');
  // Only three statuses exist: this registry tracks that a submission was
  // made, not the result of whatever legacy system processes it -- so a
  // failed credential check is not terminal, it just leaves the
  // registration "pending" and resubmittable until it expires.
  const PENDING_SERVICE_REQUEST_STATUSES = ['registered', 'submitted', 'credential_check_pending'];

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
      // `listSupportingText` is retained as a read-only fallback for messages
      // stored before the presentation-neutral extended-text contract.
      extendedText: payload.extendedText || payload.listSupportingText || '',
      responseBlocks: payload.responseBlocks || [],
      listClarificationText: payload.listClarificationText || '',
      precisionSubstituted: payload.precisionSubstituted || false,
      currentStep: payload.currentStep,
      totalSteps: payload.totalSteps,
      serviceRequestActions: payload.serviceRequestActions || [],
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

  const fetchPreferences = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/preferences/${MOCK_PROFILE.userId}`);
      const data = await res.json();
      if (!data.error && Object.keys(data).length > 0) {
        setPreferences(prev => ({ ...prev, ...data }));
      }
    } catch (error) {
      console.error(error);
    }
  };

  const fetchServiceRequestList = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/service-request/list/${MOCK_PROFILE.userId}`);
      const data = await res.json();
      setServiceRequestList(data.items || []);
    } catch (error) {
      console.error('Failed to load service request list', error);
    }
  };

  const savePreferences = async (newPrefs) => {
    try {
      setPreferences(newPrefs);
      await fetch('http://localhost:8000/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: MOCK_PROFILE.userId, ...newPrefs })
      });
      setShowPreferences(false);
    } catch (error) {
      console.error("Failed to save preferences", error);
    }
  };

  // A completed/denied service_request response arrives from its own
  // endpoint, not /api/chat, but must render identically to an ordinary
  // assistant turn -- same message shape, same transcript.
  const appendServiceRequestResultMessage = (finalBotData) => {
    if (!finalBotData || !finalBotData.text) return;
    shouldAutoScrollRef.current = true;
    setMessages(prev => [...prev, {
      id: Date.now() + 2,
      sender: 'bot',
      text: finalBotData.text,
      type: finalBotData.uiType || 'text',
      options: finalBotData.options || [],
      createdAt: new Date().toISOString()
    }]);
  };

  // Opening a form is always a fresh, deterministic lookup by special_id --
  // whether it's being shown for the first time or reopened from old chat
  // history, so a stale/expired/already-completed link degrades safely
  // instead of reopening data that no longer applies.
  const openServiceRequestForm = async (action) => {
    if (!action?.specialId) return;
    setServiceRequestNotice('');
    try {
      const res = await fetch(`http://localhost:8000/api/service-request/${action.specialId}`);
      const data = await res.json();
      if (data.status === 'pending') {
        setActiveServiceRequest({
          specialId: action.specialId,
          serviceRequestId: data.service_request_id,
          description: data.description || action.description,
          formSchema: data.form_schema || action.formSchema,
          stepIndex: 0,
          values: {}
        });
      } else if (data.status === 'completed') {
        setServiceRequestNotice(t(preferences.language, 'serviceRequest.alreadyCompleted'));
      } else if (data.status === 'expired') {
        setServiceRequestNotice(t(preferences.language, 'serviceRequest.expired'));
      } else {
        setServiceRequestNotice(t(preferences.language, 'serviceRequest.unavailable'));
      }
    } catch (error) {
      console.error('Failed to open service request form', error);
      setServiceRequestNotice(t(preferences.language, 'serviceRequest.unavailable'));
    }
  };

  const updateServiceRequestField = (fieldId, value) => {
    setActiveServiceRequest(prev => prev ? { ...prev, values: { ...prev.values, [fieldId]: value } } : prev);
  };

  const goToServiceRequestStep = (delta) => {
    setActiveServiceRequest(prev => {
      if (!prev) return prev;
      const totalSteps = prev.formSchema?.steps?.length || 1;
      const nextIndex = Math.min(Math.max(prev.stepIndex + delta, 0), totalSteps - 1);
      return { ...prev, stepIndex: nextIndex };
    });
  };

  const submitServiceRequestForm = async () => {
    if (!activeServiceRequest) return;
    setIsSubmittingServiceRequest(true);
    try {
      const res = await fetch('http://localhost:8000/api/service-request/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          special_id: activeServiceRequest.specialId,
          user_id: MOCK_PROFILE.userId,
          session_id: sessionId,
          submitted_data: activeServiceRequest.values
        })
      });
      const data = await res.json();
      if (data.status === 'credential_check_required') {
        setActiveServiceRequest(null);
        setActiveCredentialCheck({ specialId: data.special_id });
      } else if (data.status === 'error') {
        setServiceRequestNotice(data.message || t(preferences.language, 'serviceRequest.unavailable'));
        setActiveServiceRequest(null);
      } else {
        setActiveServiceRequest(null);
        appendServiceRequestResultMessage(data);
      }
    } catch (error) {
      console.error('Service request submission failed', error);
      setServiceRequestNotice(t(preferences.language, 'serviceRequest.unavailable'));
      setActiveServiceRequest(null);
    } finally {
      setIsSubmittingServiceRequest(false);
      void fetchServiceRequestList();
    }
  };

  // The mocked "Bank Centralized Credential Check" -- a real integration
  // would perform an actual re-authentication challenge and report only
  // pass/fail here; this MVP lets the demo choose the outcome directly so
  // both the granted and denied paths can be shown.
  const resolveCredentialCheck = async (passed) => {
    if (!activeCredentialCheck) return;
    setIsSubmittingServiceRequest(true);
    try {
      const res = await fetch('http://localhost:8000/api/service-request/credential-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          special_id: activeCredentialCheck.specialId,
          user_id: MOCK_PROFILE.userId,
          session_id: sessionId,
          passed
        })
      });
      const data = await res.json();
      setActiveCredentialCheck(null);
      if (data.status === 'error') {
        setServiceRequestNotice(data.message || t(preferences.language, 'serviceRequest.unavailable'));
      } else {
        appendServiceRequestResultMessage(data);
      }
    } catch (error) {
      console.error('Credential check failed', error);
      setActiveCredentialCheck(null);
      setServiceRequestNotice(t(preferences.language, 'serviceRequest.unavailable'));
    } finally {
      setIsSubmittingServiceRequest(false);
      void fetchServiceRequestList();
    }
  };

  useEffect(() => {
    if (sessionId) {
      void loadHistoryPage(sessionId, null, true);
      scheduleSessionTimeout();
    }
    fetchPreferences();
    void fetchServiceRequestList();

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
    setLoadingCaption(t(preferences.language, "caption.understanding"));

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
                  ? t(preferences.language, NODE_CAPTIONS.memory_compaction)
                  : t(preferences.language, "caption.contextReady")
              );
              continue;
            }

            // Check if the payload is a node transition update
            if (parsedData.node) {
              // Use the dictionary if it exists, otherwise show a generic processing message
              const captionKey = NODE_CAPTIONS[parsedData.node] || "caption.continuing";
              const translatedCaption = t(preferences.language, captionKey);
              console.log("🔄 [UPDATING UI STATE TO]:", translatedCaption);
              setLoadingCaption(translatedCaption);
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
            extendedText: finalBotData.extendedText || finalBotData.listSupportingText || '',
            responseBlocks: finalBotData.responseBlocks || [],
            listClarificationText: finalBotData.listClarificationText || '',
            precisionSubstituted: finalBotData.precisionSubstituted || false,
            currentStep: finalBotData.currentStep,
            totalSteps: finalBotData.totalSteps,
            serviceRequestActions: finalBotData.serviceRequestActions || [],
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
        text: t(preferences.language, "escalation.networkError"),
        type: 'escalation'
      }]);
    } finally {
      setIsCompactingMemory(false);
      setIsTyping(false);
      scheduleSessionTimeout();
      void fetchServiceRequestList();
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
      {options?.map((opt, idx) => {
        const label = typeof opt === 'string' ? opt : (opt?.label || opt?.message || t(preferences.language, "misc.continue"));
        const message = typeof opt === 'string' ? opt : (opt?.message || opt?.label || '');
        return (
          <button
            key={idx}
            onClick={() => message && handleSend(message)}
            disabled={!message || isTyping || isCompactingMemory}
            className="bg-white border border-blue-200 text-blue-700 hover:bg-blue-50 px-4 py-2 rounded-full text-sm font-medium transition-colors shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {label}
          </button>
        );
      })}
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
            <span className="text-sm font-semibold">{progressTitle || t(preferences.language, "misc.guidedProcess")}</span>
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
          const detail = item.detail || stepDetails?.[index] || (item.number === 1 ? stepDetail : '');
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
        <h4 className="font-semibold text-slate-800 text-sm">{title || t(preferences.language, "misc.keyInfo")}</h4>
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

  const renderEscalationConfirmation = (block) => {
    const confirmation = block.confirmation || {};
    const confirmationMessage = confirmation.message || '';
    return (
      <section className="mt-3 w-full max-w-sm overflow-hidden rounded-xl border border-blue-200 bg-white shadow-sm">
        <div className="border-b border-blue-100 bg-blue-50 px-4 py-3">
          <div className="flex items-center gap-2 text-blue-900">
            <UserCheck size={18} className="text-blue-600" />
            <h4 className="text-sm font-semibold">{block.title || t(preferences.language, "escalation.confirmTitle")}</h4>
          </div>
        </div>
        <div className="space-y-3 px-4 py-3.5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{block.caseTitle || t(preferences.language, "escalation.caseTitle")}</p>
            <p className="mt-2 text-sm leading-relaxed text-slate-700 whitespace-pre-wrap">
              {block.request || t(preferences.language, "escalation.fallbackDesc")}
            </p>
            <dl className="mt-2 space-y-2 text-sm leading-relaxed text-slate-700">
              {block.unresolvedIssues?.length > 0 && (
                <div>
                  <dt className="font-medium text-slate-600">Unresolved items</dt>
                  <dd className="mt-1">
                    <ul className="space-y-1.5">
                      {block.unresolvedIssues.map((issue, issueIndex) => (
                        <li key={issueIndex} className="flex gap-2">
                          <AlertCircle size={14} className="mt-1 shrink-0 text-blue-600" aria-hidden="true" />
                          <span>{issue}</span>
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              )}
            </dl>
          </div>
          <p className="border-t border-slate-100 pt-3 text-xs leading-relaxed text-slate-500">
            {block.notice || t(preferences.language, "escalation.notice")}
          </p>
          <button
            type="button"
            onClick={() => confirmationMessage && handleSend(confirmationMessage)}
            disabled={!confirmationMessage || isTyping || isCompactingMemory}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            <UserCheck size={16} />
            {confirmation.label || t(preferences.language, "escalation.confirmBtn")}
          </button>
        </div>
      </section>
    );
  };

  const renderResponseBlock = (block, messageId, index, precisionSubstituted = false) => {
    const blockId = `${messageId}-${block.blockId || index}`;
    const expandedStep = expandedProgressSteps[blockId] ?? 1;
    return (
      <div key={blockId} className={index > 0 ? 'mt-5' : ''}>
        <div className="mb-1.5 ml-1 flex items-end gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-400">
          <span>{t(preferences.language, "chat.botName")}</span>
          {index === 0 && precisionSubstituted && renderPrecisionBadge()}
        </div>
        {block.type === 'text' && (
          <div className="max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white p-3.5 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {block.contextText && (
              <div className="mb-2 text-slate-600">{block.contextText}</div>
            )}
            {block.text}
          </div>
        )}
        {block.type === 'clarification' && renderClarificationCard(block.text)}
        {block.type === 'stepper' && block.contextText && (
          <div className="mb-2 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {block.contextText}
          </div>
        )}
        {block.type === 'stepper' && renderStepper(
          block.items,
          block.progressTitle,
          block.totalSteps,
          block.options,
          '',
          block.stepDetails,
          expandedStep,
          (stepNumber) => setExpandedProgressSteps(prev => ({ ...prev, [blockId]: stepNumber }))
        )}
        {block.type === 'checklist' && block.listIntro && (
          <div className="mt-2 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {block.listIntro}
          </div>
        )}
        {block.type === 'checklist' && renderChecklist(block.items, block.listTitle, block.options)}
        {block.type === 'escalation_confirmation' && renderEscalationConfirmation(block)}
        {block.extendedText && (
          <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {block.extendedText}
          </div>
        )}
      </div>
    );
  };

  const renderEscalation = () => (
    <div className="bg-red-50 rounded-xl border border-red-200 p-4 mt-2 shadow-sm w-full max-w-sm">
      <div className="flex items-center gap-2 mb-2 text-red-600">
        <AlertCircle size={18} />
        <h4 className="font-semibold text-sm">{t(preferences.language, "escalation.guardrailTitle")}</h4>
      </div>
      <p className="text-xs text-red-700 mb-3 leading-relaxed">
        {t(preferences.language, "escalation.guardrailBody")}
      </p>
      <button className="w-full bg-slate-800 hover:bg-slate-900 text-white font-medium py-2 rounded-lg text-sm transition-colors flex items-center justify-center gap-2 shadow-sm">
        <UserCheck size={16} /> {t(preferences.language, "escalation.guardrailBtn")}
      </button>
    </div>
  );

  // The button attached to a message that registered a service_request.
  // Deterministic props (specialId/formSchema/description) came straight
  // from the backend's ui_action_payload -- never model-authored.
  const renderServiceRequestAction = (action) => (
    <button
      type="button"
      onClick={() => openServiceRequestForm(action)}
      disabled={isTyping || isCompactingMemory}
      className="mt-3 flex w-full max-w-sm items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm font-semibold text-blue-700 shadow-sm transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <FileText size={16} />
      {action.label || t(preferences.language, "serviceRequest.completeForm")}
    </button>
  );

  const renderServiceRequestField = (field) => {
    const value = activeServiceRequest?.values?.[field.field_id];
    if (field.field_type === "text") {
      return (
        <input
          type="text"
          className="w-full border border-slate-200 rounded-lg p-2 text-sm"
          value={value ?? field.default ?? ""}
          onChange={(e) => updateServiceRequestField(field.field_id, e.target.value)}
        />
      );
    }
    if (field.field_type === "options") {
      return (
        <select
          className="w-full border border-slate-200 rounded-lg p-2 text-sm"
          value={value ?? field.default ?? ""}
          onChange={(e) => updateServiceRequestField(field.field_id, e.target.value)}
        >
          <option value="" disabled>—</option>
          {(field.options || []).map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      );
    }
    if (field.field_type === "checkbox") {
      return (
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={Boolean(value ?? field.default ?? false)}
            onChange={(e) => updateServiceRequestField(field.field_id, e.target.checked)}
          />
          {field.label}
        </label>
      );
    }
    if (field.field_type === "document_reference") {
      const uploaded = Boolean(value);
      return (
        <div className="flex flex-col gap-1.5">
          <button
            type="button"
            onClick={() => updateServiceRequestField(field.field_id, `mock-doc-ref-${Date.now()}`)}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
              uploaded
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
            }`}
          >
            {uploaded ? <CheckCircle2 size={16} /> : <FileText size={16} />}
            {uploaded ? t(preferences.language, "serviceRequest.uploaded") : t(preferences.language, "serviceRequest.simulateUpload")}
          </button>
          <span className="text-[11px] text-slate-400">{t(preferences.language, "serviceRequest.mockDemoNotice")}</span>
        </div>
      );
    }
    return null;
  };

  const renderServiceRequestFormModal = () => {
    if (!activeServiceRequest) return null;
    const steps = activeServiceRequest.formSchema?.steps || [];
    const currentStep = steps[activeServiceRequest.stepIndex];
    if (!currentStep) return null;
    const isLastStep = activeServiceRequest.stepIndex === steps.length - 1;
    const isFirstStep = activeServiceRequest.stepIndex === 0;
    return (
      <div className="absolute inset-0 bg-slate-900/50 flex items-center justify-center z-50 p-6">
        <div className="bg-white rounded-2xl w-full max-w-sm shadow-xl flex flex-col max-h-[80vh] overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <div>
              <h3 className="font-bold text-slate-800">{currentStep.caption}</h3>
              <p className="text-xs text-slate-400 mt-0.5">
                {t(preferences.language, "serviceRequest.step")} {activeServiceRequest.stepIndex + 1} {t(preferences.language, "serviceRequest.of")} {steps.length}
              </p>
            </div>
            <button
              onClick={() => setActiveServiceRequest(null)}
              className="text-slate-400 hover:text-slate-600"
              title={t(preferences.language, "serviceRequest.cancel")}
            >×</button>
          </div>
          <div className="p-4 overflow-y-auto flex flex-col gap-4 text-sm">
            {(currentStep.fields || []).map((field) => (
              <div key={field.field_id}>
                {field.field_type !== "checkbox" && (
                  <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{field.label}</label>
                )}
                {renderServiceRequestField(field)}
                {field.notice && <p className="mt-1 text-[11px] text-slate-400">{field.notice}</p>}
              </div>
            ))}
          </div>
          <div className="p-4 border-t border-slate-100 flex justify-between gap-2">
            <button
              onClick={() => goToServiceRequestStep(-1)}
              disabled={isFirstStep || isSubmittingServiceRequest}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
            >
              {t(preferences.language, "serviceRequest.back")}
            </button>
            {isLastStep ? (
              <button
                onClick={submitServiceRequestForm}
                disabled={isSubmittingServiceRequest}
                className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors disabled:opacity-50"
              >
                {isSubmittingServiceRequest ? t(preferences.language, "serviceRequest.submitting") : t(preferences.language, "serviceRequest.submit")}
              </button>
            ) : (
              <button
                onClick={() => goToServiceRequestStep(1)}
                className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
              >
                {t(preferences.language, "serviceRequest.next")}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderCredentialCheckModal = () => {
    if (!activeCredentialCheck) return null;
    return (
      <div className="absolute inset-0 bg-slate-900/50 flex items-center justify-center z-50 p-6">
        <div className="bg-white rounded-2xl w-full max-w-sm shadow-xl overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center gap-2">
            <ShieldCheck size={18} className="text-blue-600" />
            <h3 className="font-bold text-slate-800 text-sm">{t(preferences.language, "serviceRequest.credentialCheckTitle")}</h3>
          </div>
          <div className="p-4 text-sm text-slate-600 leading-relaxed">
            {t(preferences.language, "serviceRequest.credentialCheckBody")}
          </div>
          <div className="p-4 border-t border-slate-100 flex flex-col gap-2">
            <button
              onClick={() => resolveCredentialCheck(true)}
              disabled={isSubmittingServiceRequest}
              className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {t(preferences.language, "serviceRequest.simulatePass")}
            </button>
            <button
              onClick={() => resolveCredentialCheck(false)}
              disabled={isSubmittingServiceRequest}
              className="border border-slate-200 text-slate-600 hover:bg-slate-50 font-medium py-2.5 rounded-lg transition-colors disabled:opacity-50"
            >
              {t(preferences.language, "serviceRequest.simulateFail")}
            </button>
          </div>
        </div>
      </div>
    );
  };

  const renderServiceRequestNotice = () => {
    if (!serviceRequestNotice) return null;
    return (
      <div className="absolute bottom-24 left-1/2 -translate-x-1/2 z-40 max-w-[85%] rounded-lg bg-slate-800 text-white text-xs px-4 py-2.5 shadow-lg flex items-center gap-2">
        <AlertCircle size={14} />
        <span>{serviceRequestNotice}</span>
        <button onClick={() => setServiceRequestNotice('')} className="ml-2 text-slate-300 hover:text-white">×</button>
      </div>
    );
  };

  const serviceRequestStatusMeta = (status) => {
    if (PENDING_SERVICE_REQUEST_STATUSES.includes(status)) {
      return { label: t(preferences.language, 'serviceRequest.statusPending'), icon: <Clock size={14} />, className: 'text-amber-700 bg-amber-50 border-amber-200' };
    }
    if (status === 'completed') {
      return { label: t(preferences.language, 'serviceRequest.statusCompleted'), icon: <CheckCircle2 size={14} />, className: 'text-emerald-700 bg-emerald-50 border-emerald-200' };
    }
    return { label: t(preferences.language, 'serviceRequest.statusExpired'), icon: <XCircle size={14} />, className: 'text-slate-500 bg-slate-50 border-slate-200' };
  };

  const renderServiceRequestListModal = () => {
    if (!showServiceRequestList) return null;

    const ITEMS_PER_PAGE = 3;
    const filteredList = serviceRequestList.filter(item => {
      if (serviceRequestTab === 'pending') return PENDING_SERVICE_REQUEST_STATUSES.includes(item.status);
      if (serviceRequestTab === 'completed') return item.status === 'completed';
      if (serviceRequestTab === 'expired') return item.status === 'expired';
      return false;
    });

    const totalPages = Math.ceil(filteredList.length / ITEMS_PER_PAGE);
    const paginatedList = filteredList.slice((serviceRequestPage - 1) * ITEMS_PER_PAGE, serviceRequestPage * ITEMS_PER_PAGE);

    return (
      <div className="absolute inset-0 bg-slate-900/50 flex items-center justify-center z-50 p-6">
        <div className="bg-white rounded-2xl w-full max-w-sm shadow-xl flex flex-col h-[500px] overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <h3 className="font-bold text-slate-800">{t(preferences.language, 'serviceRequest.listTitle')}</h3>
            <button onClick={() => { setShowServiceRequestList(false); setServiceRequestPage(1); }} className="text-slate-400 hover:text-slate-600">×</button>
          </div>
          <div className="flex border-b border-slate-100 px-4">
            <button onClick={() => { setServiceRequestTab('pending'); setServiceRequestPage(1); }} className={`flex-1 py-3 text-xs font-semibold ${serviceRequestTab === 'pending' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-slate-400 hover:text-slate-600'}`}>
              {t(preferences.language, 'serviceRequest.statusPending')}
            </button>
            <button onClick={() => { setServiceRequestTab('completed'); setServiceRequestPage(1); }} className={`flex-1 py-3 text-xs font-semibold ${serviceRequestTab === 'completed' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-slate-400 hover:text-slate-600'}`}>
              {t(preferences.language, 'serviceRequest.statusCompleted')}
            </button>
            <button onClick={() => { setServiceRequestTab('expired'); setServiceRequestPage(1); }} className={`flex-1 py-3 text-xs font-semibold ${serviceRequestTab === 'expired' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-slate-400 hover:text-slate-600'}`}>
              {t(preferences.language, 'serviceRequest.statusExpired')}
            </button>
          </div>
          <div className="overflow-y-auto flex-1">
            {filteredList.length === 0 && (
              <p className="p-4 text-sm text-slate-400 text-center">{t(preferences.language, 'serviceRequest.listEmpty')}</p>
            )}
            <ul className="divide-y divide-slate-100">
              {paginatedList.map((item) => {
                const meta = serviceRequestStatusMeta(item.status);
                const isPending = PENDING_SERVICE_REQUEST_STATUSES.includes(item.status);
                return (
                  <li key={item.special_id}>
                    <button
                      type="button"
                      disabled={!isPending}
                      onClick={() => {
                        setShowServiceRequestList(false);
                        openServiceRequestForm({ specialId: item.special_id, description: item.description });
                      }}
                      className={`w-full text-left p-4 flex flex-col gap-1.5 ${isPending ? 'hover:bg-slate-50 cursor-pointer' : 'cursor-default'}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-slate-800">{item.action_label || item.description}</span>
                        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold shrink-0 ${meta.className}`}>
                          {meta.icon}
                          {meta.label}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-2 mt-1">
                        {item.action_label && item.description && (
                          <span className="text-xs text-slate-500">{item.description}</span>
                        )}
                        {item.created_at && (
                          <span className="text-[10px] text-slate-400 font-mono">
                            {new Date(item.created_at).toLocaleString(preferences.language === 'Mandarin' ? 'zh-CN' : preferences.language === 'Spanish' ? 'es-ES' : 'en-US', {
                              year: 'numeric', month: 'short', day: 'numeric',
                              hour: '2-digit', minute: '2-digit'
                            })}
                          </span>
                        )}
                      </div>
                      {isPending && (
                        <span className="text-[11px] text-blue-600 font-medium mt-1">{t(preferences.language, 'serviceRequest.tapToComplete')}</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
          {totalPages > 1 && (
            <div className="p-3 border-t border-slate-100 flex items-center justify-between bg-slate-50">
              <button
                onClick={() => setServiceRequestPage(p => Math.max(1, p - 1))}
                disabled={serviceRequestPage === 1}
                className="px-3 py-1 text-xs font-medium text-slate-600 disabled:opacity-50 disabled:cursor-not-allowed bg-white border border-slate-200 rounded-md shadow-sm"
              >
                {t(preferences.language, 'serviceRequest.prevPage')}
              </button>
              <span className="text-xs text-slate-500 font-medium">
                {t(preferences.language, 'serviceRequest.pageInfo').replace('{current}', serviceRequestPage).replace('{total}', totalPages)}
              </span>
              <button
                onClick={() => setServiceRequestPage(p => Math.min(totalPages, p + 1))}
                disabled={serviceRequestPage === totalPages}
                className="px-3 py-1 text-xs font-medium text-slate-600 disabled:opacity-50 disabled:cursor-not-allowed bg-white border border-slate-200 rounded-md shadow-sm"
              >
                {t(preferences.language, 'serviceRequest.nextPage')}
              </button>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderPreferencesModal = () => {
    if (!showPreferences) return null;
    return (
      <div className="absolute inset-0 bg-slate-900/50 flex items-center justify-center z-50 p-6">
        <div className="bg-white rounded-2xl w-full max-w-sm shadow-xl flex flex-col max-h-[80vh] overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <h3 className="font-bold text-slate-800">{t(preferences.language, "prefs.title")}</h3>
            <button onClick={() => setShowPreferences(false)} className="text-slate-400 hover:text-slate-600">×</button>
          </div>
          <div className="p-4 overflow-y-auto flex flex-col gap-4 text-sm">
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{t(preferences.language, "prefs.nickname")}</label>
              <input type="text" className="w-full border border-slate-200 rounded-lg p-2" value={preferences.nickname} onChange={(e) => setPreferences({...preferences, nickname: e.target.value})} placeholder={t(preferences.language, "prefs.nicknamePlaceholder")} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{t(preferences.language, "prefs.language")}</label>
              <select className="w-full border border-slate-200 rounded-lg p-2" value={preferences.language} onChange={(e) => setPreferences({...preferences, language: e.target.value})}>
                <option value="English">English</option>
                <option value="Mandarin">简体中文</option>
                <option value="Spanish">Español</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{t(preferences.language, "prefs.tone")}</label>
              <select className="w-full border border-slate-200 rounded-lg p-2" value={preferences.tone} onChange={(e) => setPreferences({...preferences, tone: e.target.value})}>
                <option value="professional">{t(preferences.language, "prefs.toneProfessional")}</option>
                <option value="friendly">{t(preferences.language, "prefs.toneFriendly")}</option>
                <option value="concise">{t(preferences.language, "prefs.toneConcise")}</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{t(preferences.language, "prefs.literacy")}</label>
              <select className="w-full border border-slate-200 rounded-lg p-2" value={preferences.financial_literacy} onChange={(e) => setPreferences({...preferences, financial_literacy: e.target.value})}>
                <option value="beginner">{t(preferences.language, "prefs.literacyBeginner")}</option>
                <option value="intermediate">{t(preferences.language, "prefs.literacyIntermediate")}</option>
                <option value="professional">{t(preferences.language, "prefs.literacyProfessional")}</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{t(preferences.language, "prefs.emoji")}</label>
              <select className="w-full border border-slate-200 rounded-lg p-2" value={preferences.emoji} onChange={(e) => setPreferences({...preferences, emoji: e.target.value})}>
                <option value="enabled">{t(preferences.language, "prefs.emojiEnabled")}</option>
                <option value="disabled">{t(preferences.language, "prefs.emojiDisabled")}</option>
              </select>
            </div>
          </div>
          <div className="p-4 border-t border-slate-100 flex justify-end">
            <button onClick={() => savePreferences(preferences)} className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors">
              Save Preferences
            </button>
          </div>
        </div>
      </div>
    );
  };

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
                <h1 className="font-bold text-slate-800 text-lg leading-tight">{t(preferences.language, "header.title")}</h1>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  <span className="text-[11px] text-slate-500 font-medium uppercase tracking-wide">{t(preferences.language, "header.secureSession")}</span>
                </div>
              </div>
            </div>
            {/* Action Toggles */}
            <div className="flex items-center gap-1">
              {isLoggedIn && (
                <button
                  onClick={() => { void fetchServiceRequestList(); setShowServiceRequestList(true); }}
                  className="relative p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors"
                  title={t(preferences.language, "serviceRequest.listTitle")}
                >
                  <ClipboardList size={18}/>
                  {serviceRequestList.filter(item => PENDING_SERVICE_REQUEST_STATUSES.includes(item.status)).length > 0 && (
                    <span className="absolute top-0.5 right-0.5 bg-blue-600 text-white text-[9px] font-bold rounded-full min-w-[15px] h-[15px] flex items-center justify-center px-1">
                      {serviceRequestList.filter(item => PENDING_SERVICE_REQUEST_STATUSES.includes(item.status)).length}
                    </span>
                  )}
                </button>
              )}
              {isLoggedIn && (
                <button onClick={() => setShowPreferences(true)} className="p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors" title={t(preferences.language, "header.preferences")}>
                  <Settings size={18}/>
                </button>
              )}
              <button onClick={handleLoginToggle} className="p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors" title={t(preferences.language, "header.toggleUser")}>
                {isLoggedIn ? <LogOut size={18}/> : <LogIn size={18}/>}
              </button>
            </div>
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
                   <span className="text-xs font-medium text-slate-600">{t(preferences.language, "misc.guestUser")}</span>
                 </>
               )}
             </div>
             {isLoggedIn && <span className="text-[10px] font-mono text-slate-500 bg-white px-2 py-0.5 rounded border border-slate-200">{t(preferences.language, "misc.maskedAcc")}</span>}
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
             <p><span dangerouslySetInnerHTML={{ __html: t(preferences.language, "chat.mvpTip") }} /></p>
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
                    {msg.sender === 'bot' && msg.type !== 'blocks' && (
                      <div className="flex items-end gap-2 mb-1.5 ml-1">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{t(preferences.language, "chat.botName")}</span>
                        {msg.precisionSubstituted && renderPrecisionBadge()}
                      </div>
                    )}

                    {msg.type !== 'stepper' && msg.type !== 'checklist' && msg.type !== 'blocks' && (
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
                    {msg.type === 'blocks' && msg.responseBlocks?.map((block, blockIndex) =>
                      renderResponseBlock(block, msg.id, blockIndex, msg.precisionSubstituted)
                    )}
                    {msg.serviceRequestActions?.map((action) => (
                      <React.Fragment key={action.specialId}>
                        {renderServiceRequestAction(action)}
                      </React.Fragment>
                    ))}
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
                    {msg.type === 'stepper' && msg.extendedText && (
                      <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {msg.extendedText}
                      </div>
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
                    {msg.type === 'checklist' && msg.extendedText && (
                      <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {msg.extendedText}
                      </div>
                    )}
                    {msg.type === 'checklist' && msg.listClarificationText && renderClarificationCard(msg.listClarificationText)}
                    {(msg.type === 'stepper' || msg.type === 'checklist' || msg.type === 'blocks') && (
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
              placeholder={t(preferences.language, "chat.placeholder")}
              className="w-full bg-slate-100 text-slate-800 text-[15px] rounded-full pl-5 pr-12 py-3.5 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all placeholder:text-slate-400"
              disabled={isTyping || !isLoggedIn || Boolean(activeServiceRequest || activeCredentialCheck)}
            />
            <button
              onClick={() => handleSend(inputValue)}
              disabled={!inputValue.trim() || isTyping || !isLoggedIn || Boolean(activeServiceRequest || activeCredentialCheck)}
              className="absolute right-1.5 bg-blue-600 text-white w-10 h-10 rounded-full flex items-center justify-center disabled:opacity-50 disabled:bg-slate-300 hover:bg-blue-700 transition-colors shadow-sm"
            >
              <Send size={18} className="ml-0.5" />
            </button>
          </div>
          <div className="flex items-center justify-center gap-1 mt-3 mb-1">
             <LockIcon size={10} className="text-slate-400"/>
             <span className="text-[10px] text-slate-400 font-medium tracking-wide uppercase">{t(preferences.language, "chat.footer")}</span>
          </div>
        </div>
        {renderPreferencesModal()}
        {renderServiceRequestFormModal()}
        {renderCredentialCheckModal()}
        {renderServiceRequestListModal()}
        {renderServiceRequestNotice()}
      </div>
      
      {/* Hide Scrollbars CSS */}
      <style dangerouslySetInnerHTML={{__html: `
        .hide-scrollbar::-webkit-scrollbar { display: none; }
        .hide-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      `}} />
    </div>
  );
}
