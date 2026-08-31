import React, { useState, useEffect, useRef } from 'react';
import { t } from './translations';
import {
  Send, Bot, ChevronRight, CheckCircle2,
  CreditCard, Wallet, Landmark, ShieldCheck,
  FileText, UserCheck, AlertCircle, LogIn, LogOut, Lock as LockIcon,
  Loader2, Settings, ClipboardList, Clock, XCircle, ArrowRight, ArrowLeft, LayoutDashboard,
  ThumbsUp, ThumbsDown, Copy, Check, Sparkles, ChevronDown, ChevronUp, AlertTriangle, ShieldAlert, Info, Layers, HelpCircle
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
  "n21_audit": "caption.n21_audit",
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

// WP-1/WP-3: the backend never resolves a {{data-*}} token into visible
// text -- it only ever sends the token, plus (separately, this turn only)
// the token->value pairs revealed this turn. The tokens are preserved in
// message state and rendered dynamically as secure chips by renderTextWithDataChips.
// Active tokens render as protected data chips with a shield icon and privacy tooltip;
// expired tokens or missing values render as an expired data chip.

export default function BankingBotUI() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(ACTIVE_SESSION_KEY));
  const [isLoggedIn, setIsLoggedIn] = useState(() => Boolean(localStorage.getItem(ACTIVE_SESSION_KEY)));
  const [messages, setMessages] = useState([]);
  // Ratings the customer has already submitted this session, keyed by
  // message id -- deliberately plain useState like personalDataStore below:
  // a reload losing this is fine, it only suppresses re-showing thumbs on a
  // message already rated, and the durable record lives server-side.
  const [turnRatings, setTurnRatings] = useState({});
  // In-memory, session-scoped {{data-*}} token -> real value map (WP-1/WP-3).
  // Deliberately plain useState, not persisted to localStorage or any
  // backend store: the backend never resolves this token into chat text, so
  // a page reload losing this map is the correct behavior -- any
  // {{data-*}} token this map no longer has a value for renders the generic
  // "protected, no longer available" fallback instead of raw braces or a
  // stale value. See resolvePersonalDataTokens below.
  const [personalDataStore, setPersonalDataStore] = useState({});

  const [inputValue, setInputValue] = useState('');
  const [isInputHovered, setIsInputHovered] = useState(false);
  const [isInputFocused, setIsInputFocused] = useState(false);
  // Backend-generated only (node_04b), never a static/mock list -- each
  // suggestion is required to trace back to a specific piece of this
  // turn's own already-approved evidence that the response didn't feature,
  // so tapping one can never dead-end into "I don't have that." Driven by
  // the LATEST turn's own value, same lifecycle as softEscalationAvailable.
  const [nextActions, setNextActions] = useState([]);
  const [isTyping, setIsTyping] = useState(false);
  const [isCompactingMemory, setIsCompactingMemory] = useState(false);
  const [loadingCaption, setLoadingCaption] = useState(t("English", "caption.processing")); // <-- NEW STATE FOR TRACKER
  const [isLowConfidenceMode, setIsLowConfidenceMode] = useState(false);
  const [taskConfidence, setTaskConfidence] = useState(null);
  // Driven by the LATEST turn's own value only (see setSoftEscalationAvailable
  // below), never attached to a past message -- unlike a chip baked into
  // chat history, this banner disappears the moment a turn comes back with
  // node_08's signal no longer triggered, matching how the backend itself
  // recomputes it fresh every turn rather than persisting a sticky flag.
  const [softEscalationAvailable, setSoftEscalationAvailable] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [expandedProgressSteps, setExpandedProgressSteps] = useState({});
  const [expandedAccordions, setExpandedAccordions] = useState({});
  const [copiedKey, setCopiedKey] = useState(null);
  const [showPreferences, setShowPreferences] = useState(false);
  const [preferences, setPreferences] = useState({
    nickname: "",
    language: "English",
    tone: "professional",
    emoji: "enabled",
    financial_literacy: "intermediate"
  });

  // --- WP-7: Embedded Interactive Service Request state ---
  // embeddedFormStates holds in-place step indices, values, submission states,
  // and inline credential challenges keyed by specialId.
  const [embeddedFormStates, setEmbeddedFormStates] = useState({});
  const [activeServiceRequest, setActiveServiceRequest] = useState(null);
  const [activeCredentialCheck, setActiveCredentialCheck] = useState(null);
  const [serviceRequestNotice, setServiceRequestNotice] = useState('');
  const [isSubmittingServiceRequest, setIsSubmittingServiceRequest] = useState(false);
  // The to-do list: every service request the customer has ever made,
  // scoped by user_id so it persists across sessions -- pending ones are
  // reopenable, completed/expired ones are shown for reference only.
  const [serviceRequestList, setServiceRequestList] = useState([]);
  const [showServiceRequestList, setShowServiceRequestList] = useState(false);
  const [activeDeeplink, setActiveDeeplink] = useState(null);
  const [serviceRequestPage, setServiceRequestPage] = useState(1);
  const [serviceRequestTab, setServiceRequestTab] = useState('pending');
  // Only three statuses exist: this registry tracks that a submission was
  // made, not the result of whatever legacy system processes it -- so a
  // failed credential check is not terminal, it just leaves the
  // registration "pending" and resubmittable until it expires.
  const PENDING_SERVICE_REQUEST_STATUSES = ['pending', 'registered', 'submitted', 'credential_check_pending'];

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

  const hoverTimeoutRef = useRef(null);

  const handleInputMouseEnter = () => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
    setIsInputHovered(true);
  };

  const handleInputMouseLeave = () => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
    hoverTimeoutRef.current = setTimeout(() => {
      setIsInputHovered(false);
    }, 250);
  };

  const [isBannerHovered, setIsBannerHovered] = useState(false);
  const bannerHoverTimeoutRef = useRef(null);

  const handleBannerMouseEnter = () => {
    if (bannerHoverTimeoutRef.current) {
      clearTimeout(bannerHoverTimeoutRef.current);
      bannerHoverTimeoutRef.current = null;
    }
    if (isLoggedIn) {
      void fetchServiceRequestList();
      setIsBannerHovered(true);
    }
  };

  const handleBannerMouseLeave = () => {
    if (bannerHoverTimeoutRef.current) {
      clearTimeout(bannerHoverTimeoutRef.current);
    }
    bannerHoverTimeoutRef.current = setTimeout(() => {
      setIsBannerHovered(false);
    }, 250);
  };


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
      personalDataSubstituted: payload.personalDataSubstituted || false,
      currentStep: payload.currentStep,
      totalSteps: payload.totalSteps,
      serviceRequestActions: payload.serviceRequestActions || [],
      navigationActions: payload.navigationActions || [],
      showFeedbackPrompt: payload.showFeedbackPrompt || false,
      turnNumber: payload.turnNumber,
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
      const historyMessages = page.messages
        .filter((message) => !message.content?.startsWith('[Completed:') && !message.content?.startsWith('[Denied:'))
        .map((message) => toUiMessage(message, targetSessionId));
      const container = chatContainerRef.current;
      const previousHeight = container?.scrollHeight ?? 0;
      const previousTop = container?.scrollTop ?? 0;
      if (!replace) shouldAutoScrollRef.current = false;

      setMessages((currentMessages) => {
        if (replace) {
          // Never clobber messages that arrived after this fetch was kicked
          // off -- this call is fire-and-forget on the init path, so a
          // customer message (and its reply) sent while it was still in
          // flight must not be wiped out by an authoritative-looking but
          // now-stale page once this resolves.
          return currentMessages.length > historyMessages.length ? currentMessages : historyMessages;
        }
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
      const historyMessages = page.messages
        .filter((message) => !message.content?.startsWith('[Completed:') && !message.content?.startsWith('[Denied:'))
        .map((message) => toUiMessage(message, targetSessionId));
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

  // Dynamically auto-resize textarea height to fit multiline queries up to 140px
  useEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    const nextHeight = Math.min(textarea.scrollHeight, 140);
    textarea.style.height = `${nextHeight}px`;
  }, [inputValue]);

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

  // Only offered by the backend (showFeedbackPrompt) when node_08's
  // schema-derived confidence for that specific turn was itself ambiguous.
  // Optimistically marks the message rated so the buttons disappear even if
  // the request is still in flight; a failure just means the durable log
  // and the live confidence adjustment didn't happen, which is a silent,
  // low-stakes miss rather than something worth retrying or alerting on.
  const submitTurnRating = async (messageId, turnNumber, rating) => {
    setTurnRatings(prev => ({ ...prev, [messageId]: rating }));
    try {
      await fetch('http://localhost:8000/api/feedback/turn-rating', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          user_id: MOCK_PROFILE.userId,
          turn_number: turnNumber,
          rating,
        }),
      });
    } catch (error) {
      console.error('Failed to submit turn rating', error);
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

  const updateEmbeddedFormField = (specialId, fieldId, value) => {
    setEmbeddedFormStates(prev => {
      const current = prev[specialId] || { stepIndex: 0, values: {}, status: 'pending' };
      return {
        ...prev,
        [specialId]: {
          ...current,
          values: { ...current.values, [fieldId]: value }
        }
      };
    });
  };

  const goToEmbeddedFormStep = (specialId, delta, totalSteps) => {
    setEmbeddedFormStates(prev => {
      const current = prev[specialId] || { stepIndex: 0, values: {}, status: 'pending' };
      const nextIndex = Math.min(Math.max((current.stepIndex || 0) + delta, 0), Math.max(0, totalSteps - 1));
      return {
        ...prev,
        [specialId]: {
          ...current,
          stepIndex: nextIndex
        }
      };
    });
  };

  const submitEmbeddedForm = async (action) => {
    const specialId = action.specialId;
    const current = embeddedFormStates[specialId] || { stepIndex: 0, values: {} };

    setEmbeddedFormStates(prev => ({
      ...prev,
      [specialId]: { ...current, isSubmitting: true, errorMessage: '' }
    }));

    try {
      const res = await fetch('http://localhost:8000/api/service-request/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          special_id: specialId,
          user_id: MOCK_PROFILE.userId,
          session_id: sessionId,
          submitted_data: current.values || {}
        })
      });
      const data = await res.json();
      if (data.status === 'credential_check_required') {
        setEmbeddedFormStates(prev => ({
          ...prev,
          [specialId]: { ...prev[specialId], isSubmitting: false, status: 'credential_check_required' }
        }));
      } else if (data.status === 'error') {
        setEmbeddedFormStates(prev => ({
          ...prev,
          [specialId]: { ...prev[specialId], isSubmitting: false, status: 'error', errorMessage: data.message || t(preferences.language, 'serviceRequest.unavailable') }
        }));
      } else {
        setEmbeddedFormStates(prev => ({
          ...prev,
          [specialId]: { ...prev[specialId], isSubmitting: false, status: 'completed' }
        }));
        appendServiceRequestResultMessage(data);
      }
    } catch (error) {
      console.error('Service request submission failed', error);
      setEmbeddedFormStates(prev => ({
        ...prev,
        [specialId]: { ...prev[specialId], isSubmitting: false, status: 'error', errorMessage: t(preferences.language, 'serviceRequest.unavailable') }
      }));
    } finally {
      void fetchServiceRequestList();
    }
  };

  const resolveEmbeddedCredentialCheck = async (specialId, passed) => {
    const current = embeddedFormStates[specialId] || {};
    setEmbeddedFormStates(prev => ({
      ...prev,
      [specialId]: { ...current, isSubmitting: true, errorMessage: '' }
    }));

    try {
      const res = await fetch('http://localhost:8000/api/service-request/credential-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          special_id: specialId,
          user_id: MOCK_PROFILE.userId,
          session_id: sessionId,
          passed
        })
      });
      const data = await res.json();
      if (data.status === 'error') {
        setEmbeddedFormStates(prev => ({
          ...prev,
          [specialId]: { ...prev[specialId], isSubmitting: false, status: 'error', errorMessage: data.message || t(preferences.language, 'serviceRequest.unavailable') }
        }));
      } else {
        setEmbeddedFormStates(prev => ({
          ...prev,
          [specialId]: { ...prev[specialId], isSubmitting: false, status: passed ? 'completed' : 'denied' }
        }));
        appendServiceRequestResultMessage(data);
      }
    } catch (error) {
      console.error('Credential check failed', error);
      setEmbeddedFormStates(prev => ({
        ...prev,
        [specialId]: { ...prev[specialId], isSubmitting: false, status: 'error', errorMessage: t(preferences.language, 'serviceRequest.unavailable') }
      }));
    } finally {
      void fetchServiceRequestList();
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
    setIsLowConfidenceMode(false);
    setTaskConfidence(null);
    setSoftEscalationAvailable(false);
    setNextActions([]);

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
              if (parsedData.lowConfidenceMode) {
                setIsLowConfidenceMode(true);
              }
              // Not sticky like lowConfidenceMode: the backend recomputes
              // this fresh every retry round and resends the latest known
              // value on every progress event (null until RAG's first
              // round completes, then holding steady through the rest of
              // the turn) -- so just take whatever it says.
              setTaskConfidence(parsedData.taskConfidence ?? null);
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
        setSoftEscalationAvailable(finalBotData.softEscalationAvailable || false);
        setNextActions(finalBotData.nextActions || []);
        if (finalBotData.personalDataReveals?.length) {
          setPersonalDataStore(prev => {
            const next = { ...prev };
            for (const reveal of finalBotData.personalDataReveals) {
              next[reveal.token] = reveal.value;
            }
            return next;
          });
        }
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
            personalDataSubstituted: finalBotData.personalDataSubstituted || false,
            currentStep: finalBotData.currentStep,
            totalSteps: finalBotData.totalSteps,
            serviceRequestActions: finalBotData.serviceRequestActions || [],
            navigationActions: finalBotData.navigationActions || [],
            showFeedbackPrompt: finalBotData.showFeedbackPrompt || false,
            turnNumber: finalBotData.turnNumber,
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
        // Fire-and-forget, not awaited: the greeting is already visible from
        // finalBotData above, so there is nothing left for the customer to
        // wait on -- this reconciliation and the pagination-metadata fetch
        // can finish quietly in the background instead of holding the
        // loading spinner up through a second full network round-trip that,
        // for a brand-new login, returns the exact same single message.
        if (isInitialization) {
          void loadHistoryPage(targetSessionId, null, true);
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

  const renderChips = (options) => {
    if (!options || options.length === 0) return null;
    return (
      <div className="flex flex-wrap gap-1.5 mt-2.5 max-w-sm">
        {options.map((opt, idx) => {
          const label = typeof opt === 'string' ? opt : (opt?.label || opt?.message || t(preferences.language, "misc.continue"));
          const message = typeof opt === 'string' ? opt : (opt?.message || opt?.label || '');
          return (
            <button
              key={idx}
              type="button"
              onClick={() => message && handleSend(message)}
              disabled={!message || isTyping || isCompactingMemory}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-medium text-slate-700 bg-white border border-slate-200/90 shadow-2xs hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 active:scale-[0.98] transition-all disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer max-w-full text-left"
            >
              <span className="whitespace-normal break-words leading-relaxed">{label}</span>
            </button>
          );
        })}
      </div>
    );
  };

  const renderTurnFeedback = (msg) => {
    if (!msg.showFeedbackPrompt) return null;
    const submitted = turnRatings[msg.id];
    return (
      <div className="flex items-center gap-0.5">
        <button
          type="button"
          onClick={() => submitTurnRating(msg.id, msg.turnNumber, 1)}
          disabled={submitted !== undefined}
          title={submitted === 1 ? t(preferences.language, "feedback.thanks") : "Thumbs up"}
          aria-label="Thumbs up"
          className={`rounded p-1 transition-colors ${
            submitted === 1
              ? "text-emerald-600 bg-emerald-50"
              : submitted !== undefined
                ? "text-slate-300 cursor-default"
                : "text-slate-400 hover:text-emerald-600 hover:bg-slate-100"
          }`}
        >
          <ThumbsUp size={12} />
        </button>
        <button
          type="button"
          onClick={() => submitTurnRating(msg.id, msg.turnNumber, 0)}
          disabled={submitted !== undefined}
          title={submitted === 0 ? t(preferences.language, "feedback.thanks") : "Thumbs down"}
          aria-label="Thumbs down"
          className={`rounded p-1 transition-colors ${
            submitted === 0
              ? "text-red-600 bg-red-50"
              : submitted !== undefined
                ? "text-slate-300 cursor-default"
                : "text-slate-400 hover:text-red-600 hover:bg-slate-100"
          }`}
        >
          <ThumbsDown size={12} />
        </button>
      </div>
    );
  };

  const renderPrecisionBadge = () => (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700 normal-case tracking-normal">
      <CheckCircle2 size={10} />
      Number accurate
    </span>
  );

  const renderDataProtectedBadge = () => (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold text-blue-700 normal-case tracking-normal">
      <ShieldCheck size={10} />
      Data protected
    </span>
  );

  const renderExpiredDataChip = (key) => (
    <span key={key} className="mx-1 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-100/80 px-2 py-0.5 align-middle text-[12px] font-medium text-slate-500 shadow-sm">
      <LockIcon size={11} className="text-slate-400 shrink-0" />
      <span>{t(preferences.language, "personalData.protectedExpired")}</span>
    </span>
  );

  const handleCopyPersonalData = (value, key, e) => {
    e.stopPropagation();
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value);
    } else {
      const textArea = document.createElement("textarea");
      textArea.value = value;
      textArea.style.position = "fixed";
      textArea.style.left = "-999999px";
      textArea.style.top = "-999999px";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      try {
        document.execCommand('copy');
      } catch (err) {
        console.error('Fallback copy failed', err);
      }
      textArea.remove();
    }
    setCopiedKey(key);
    setTimeout(() => {
      setCopiedKey((prev) => (prev === key ? null : prev));
    }, 2000);
  };

  const renderProtectedDataChip = (key, value) => {
    const isCopied = copiedKey === key;
    return (
      <span
        key={key}
        className="group relative inline cursor-help rounded px-0.5 underline decoration-dotted decoration-blue-400/80 decoration-1 underline-offset-[3px] transition-all hover:bg-blue-50/80 hover:decoration-blue-600 hover:ring-1 hover:ring-blue-200/70 box-decoration-clone"
      >
        <span>{value}</span>
        <button
          type="button"
          onClick={(e) => handleCopyPersonalData(value, key, e)}
          title={t(preferences.language, isCopied ? "misc.copied" : "misc.copy")}
          aria-label={t(preferences.language, isCopied ? "misc.copied" : "misc.copy")}
          className="inline-flex items-center justify-center ml-1 p-0.5 align-baseline rounded text-slate-400 hover:text-blue-600 hover:bg-blue-100/70 transition-colors no-underline cursor-pointer"
          style={{ textDecoration: 'none' }}
        >
          {isCopied ? (
            <Check size={11} className="text-emerald-600 shrink-0" />
          ) : (
            <Copy size={11} className="shrink-0" />
          )}
        </button>
        <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-56 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-1.5 text-[11px] font-normal normal-case leading-snug text-slate-100 opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100 text-center">
          {t(preferences.language, "misc.dataProtectedTooltip")}
          <span className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-slate-900" />
        </span>
      </span>
    );
  };

  const renderTextWithDataChips = (text) => {
    if (typeof text !== 'string') return text;
    const fallbackLabel = t(preferences.language, "personalData.protectedExpired");
    const hasTokens = text.includes('{{data-');
    const hasFallback = Boolean(fallbackLabel && text.includes(fallbackLabel));
    if (!hasTokens && !hasFallback) return text;

    const tokenPattern = /(\{\{data-[^}]+\}\})/g;
    const segments = text.split(tokenPattern);
    const nodes = [];

    segments.forEach((segment, i) => {
      if (!segment) return;
      if (segment.startsWith('{{data-') && segment.endsWith('}}')) {
        if (Object.prototype.hasOwnProperty.call(personalDataStore, segment)) {
          nodes.push(renderProtectedDataChip(`prot-${i}`, personalDataStore[segment]));
        } else {
          nodes.push(renderExpiredDataChip(`exp-${i}`));
        }
      } else if (hasFallback && segment.includes(fallbackLabel)) {
        const subSegments = segment.split(fallbackLabel);
        subSegments.forEach((sub, j) => {
          if (sub) nodes.push(<React.Fragment key={`text-${i}-${j}`}>{sub}</React.Fragment>);
          if (j < subSegments.length - 1) {
            nodes.push(renderExpiredDataChip(`chip-${i}-${j}`));
          }
        });
      } else {
        nodes.push(<React.Fragment key={`text-${i}`}>{segment}</React.Fragment>);
      }
    });

    return nodes;
  };

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
                    {renderTextWithDataChips(item.label)}
                  </span>
                  <ChevronRight size={16} className={`mt-0.5 shrink-0 text-slate-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
                </button>
                {isExpanded && detail && (
                  <p className="mt-1.5 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-2 text-sm leading-relaxed text-slate-700 whitespace-pre-wrap">
                    {renderTextWithDataChips(detail)}
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

  const renderClarificationCard = (text, options = []) => (
    <div className="mt-3">
      <div className="max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 shadow-sm">
        <div className="flex items-start gap-2.5">
          <HelpCircle size={17} className="mt-0.5 shrink-0 text-blue-600" aria-hidden="true" />
          <span className="whitespace-pre-wrap">{renderTextWithDataChips(text)}</span>
        </div>
      </div>
      {options?.length > 0 && <div className="mt-2.5">{renderChips(options)}</div>}
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
            <span className="text-sm text-slate-600 leading-snug">{renderTextWithDataChips(item)}</span>
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

  // 1. Mini Comparison Table (Side-by-Side Product/Plan Comparison)
  const renderComparisonTable = (block) => {
    const headers = block.headers || [];
    const rows = block.rows || [];
    const actions = block.actions || [];
    return (
      <div className="mt-2.5 w-full max-w-sm overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
        {block.title && (
          <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-3.5 py-2.5">
            <Layers size={15} className="text-blue-600 shrink-0" />
            <h4 className="text-[13px] font-semibold text-slate-800">{block.title}</h4>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            {headers.length > 0 && (
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100/70">
                  {headers.map((head, idx) => (
                    <th key={idx} className={`p-2.5 font-bold text-slate-700 ${idx === 0 ? 'bg-slate-100/90' : 'text-center'}`}>
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody className="divide-y divide-slate-100">
              {rows.map((row, rIdx) => (
                <tr key={rIdx} className={rIdx % 2 === 0 ? 'bg-white' : 'bg-slate-50/40'}>
                  <td className="p-2.5 font-semibold text-slate-700 bg-slate-50/60 border-r border-slate-100 whitespace-nowrap">
                    {row.feature || row.label}
                  </td>
                  {(row.values || []).map((val, vIdx) => (
                    <td key={vIdx} className="p-2.5 text-center text-slate-600 leading-snug">
                      {renderTextWithDataChips(String(val))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {actions.length > 0 && (
          <div className="border-t border-slate-100 bg-slate-50/60 p-2.5 flex items-center justify-end gap-2 flex-wrap">
            {actions.map((act, aIdx) => (
              <button
                key={aIdx}
                type="button"
                onClick={() => handleSend(act.message || act.action || act.label)}
                disabled={isTyping}
                className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium transition-colors shadow-2xs disabled:opacity-50"
              >
                {act.label}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  // 3. Accordion FAQ (Collapsible Policy & Details Drawer)
  const renderAccordionFaq = (block, blockId) => {
    const sections = block.sections || block.items || [];
    return (
      <div className="mt-2.5 w-full max-w-sm overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
        {block.title && (
          <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-3.5 py-2.5">
            <FileText size={15} className="text-blue-600 shrink-0" />
            <h4 className="text-[13px] font-semibold text-slate-800">{block.title}</h4>
          </div>
        )}
        <div className="divide-y divide-slate-100">
          {sections.map((section, sIdx) => {
            const itemKey = `${blockId}-sec-${sIdx}`;
            const isOpen = expandedAccordions[itemKey] ?? (sIdx === 0);
            return (
              <div key={sIdx} className="transition-colors">
                <button
                  type="button"
                  onClick={() => setExpandedAccordions(prev => ({ ...prev, [itemKey]: !isOpen }))}
                  className="w-full px-3.5 py-2.5 flex items-center justify-between text-left hover:bg-slate-50/70 transition-colors"
                >
                  <span className="text-[13px] font-medium text-slate-800">{section.title || section.heading}</span>
                  {isOpen ? <ChevronUp size={15} className="text-slate-400 shrink-0" /> : <ChevronDown size={15} className="text-slate-400 shrink-0" />}
                </button>
                {isOpen && (
                  <div className="px-3.5 pb-3 text-xs leading-relaxed text-slate-600 whitespace-pre-wrap bg-slate-50/30 border-t border-slate-50">
                    {renderTextWithDataChips(section.content || section.detail || section.text || '')}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // 5. Timeline Tracker (Application / Request Milestone Status Tracker)
  const renderTimelineTracker = (block) => {
    const steps = block.steps || block.items || [];
    return (
      <div className="mt-2.5 w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100 mb-3.5">
          <div className="flex items-center gap-2">
            <Clock size={16} className="text-blue-600 shrink-0" />
            <div>
              <h4 className="text-[13px] font-semibold text-slate-800">{block.title || "Status Tracker"}</h4>
              {block.subtitle && <p className="text-[11px] text-slate-400 mt-0.5">{block.subtitle}</p>}
            </div>
          </div>
        </div>
        <div className="space-y-3 relative pl-1">
          {steps.map((step, idx) => {
            const isCompleted = step.status === 'completed' || step.status === 'done';
            const isActive = step.status === 'active' || step.status === 'current' || step.status === 'in_progress';
            const isLast = idx === steps.length - 1;
            return (
              <div key={idx} className="relative flex items-start gap-3">
                {!isLast && (
                  <div
                    className={`absolute left-[9px] top-5 bottom-0 w-0.5 ${isCompleted ? 'bg-emerald-400' : 'bg-slate-200'}`}
                  />
                )}
                <div className="relative z-10 mt-0.5">
                  {isCompleted ? (
                    <div className="w-5 h-5 rounded-full bg-emerald-500 text-white flex items-center justify-center shadow-xs">
                      <Check size={11} className="stroke-[3]" />
                    </div>
                  ) : isActive ? (
                    <div className="w-5 h-5 rounded-full bg-blue-500 text-white flex items-center justify-center animate-pulse shadow-xs">
                      <div className="w-2 h-2 rounded-full bg-white" />
                    </div>
                  ) : (
                    <div className="w-5 h-5 rounded-full bg-slate-100 border border-slate-300 flex items-center justify-center">
                      <div className="w-1.5 h-1.5 rounded-full bg-slate-300" />
                    </div>
                  )}
                </div>
                <div className="flex-1 min-w-0 pb-1">
                  <div className="flex items-center justify-between">
                    <h5 className={`text-xs font-semibold ${isActive ? 'text-blue-600' : isCompleted ? 'text-slate-800' : 'text-slate-400'}`}>
                      {step.title}
                    </h5>
                    {step.date && <span className="text-[10px] text-slate-400">{step.date}</span>}
                  </div>
                  {step.description && (
                    <p className="text-[11px] text-slate-500 mt-0.5 leading-snug">
                      {renderTextWithDataChips(step.description)}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // 6. Horizontal Swipeable Carousel (Product & Offers Gallery)
  const renderHorizontalCarousel = (block) => {
    const items = block.items || [];
    return (
      <div className="mt-2.5 w-full max-w-sm space-y-2">
        {block.title && (
          <div className="flex items-center gap-1.5 px-1">
            <Sparkles size={14} className="text-blue-600" />
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-600">{block.title}</h4>
          </div>
        )}
        <div className="flex gap-2.5 overflow-x-auto pb-2 pt-0.5 px-0.5 snap-x hide-scrollbar">
          {items.map((item, idx) => (
            <div
              key={idx}
              className="min-w-[220px] max-w-[240px] shrink-0 snap-start rounded-2xl border border-slate-200 bg-white p-3.5 shadow-xs flex flex-col justify-between"
            >
              <div>
                {item.badge && (
                  <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-100 mb-2">
                    {item.badge}
                  </span>
                )}
                <h5 className="text-sm font-bold text-slate-800 line-clamp-1">{item.title}</h5>
                {item.subtitle && <p className="text-[11px] text-slate-500 line-clamp-1 mt-0.5">{item.subtitle}</p>}
                {item.highlight && (
                  <div className="mt-2 rounded-lg bg-blue-50/80 p-2 text-xs font-semibold text-blue-700 border border-blue-100/60">
                    {item.highlight}
                  </div>
                )}
                {item.details?.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {item.details.map((det, dIdx) => (
                      <li key={dIdx} className="text-[11px] text-slate-600 flex items-start gap-1.5 leading-snug">
                        <CheckCircle2 size={12} className="text-emerald-500 shrink-0 mt-0.5" />
                        <span className="line-clamp-2">{det}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleSend(item.actionMessage || item.actionLabel || item.title)}
                disabled={isTyping}
                className="mt-3 w-full rounded-xl bg-slate-900 hover:bg-blue-600 text-white text-xs font-semibold py-2 transition-colors shadow-2xs"
              >
                {item.actionLabel || "Select"}
              </button>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // 7. Actionable Alert Callout Banner
  const renderActionableAlert = (block) => {
    const severity = block.severity || 'warning';
    const styles = {
      security: {
        bg: 'bg-red-50',
        border: 'border-red-200',
        text: 'text-red-900',
        icon: <ShieldAlert size={18} className="text-red-600 shrink-0 mt-0.5" />,
        btn: 'bg-red-600 hover:bg-red-700 text-white',
      },
      warning: {
        bg: 'bg-amber-50',
        border: 'border-amber-200',
        text: 'text-amber-900',
        icon: <AlertTriangle size={18} className="text-amber-600 shrink-0 mt-0.5" />,
        btn: 'bg-amber-600 hover:bg-amber-700 text-white',
      },
      info: {
        bg: 'bg-blue-50',
        border: 'border-blue-200',
        text: 'text-blue-900',
        icon: <Info size={18} className="text-blue-600 shrink-0 mt-0.5" />,
        btn: 'bg-blue-600 hover:bg-blue-700 text-white',
      },
      success: {
        bg: 'bg-emerald-50',
        border: 'border-emerald-200',
        text: 'text-emerald-900',
        icon: <CheckCircle2 size={18} className="text-emerald-600 shrink-0 mt-0.5" />,
        btn: 'bg-emerald-600 hover:bg-emerald-700 text-white',
      }
    };
    const currentStyle = styles[severity] || styles.warning;
    return (
      <div className={`mt-2.5 w-full max-w-sm rounded-2xl border ${currentStyle.border} ${currentStyle.bg} p-3.5 shadow-xs`}>
        <div className="flex items-start gap-2.5">
          {currentStyle.icon}
          <div className="flex-1 min-w-0">
            {block.title && <h4 className={`text-xs font-bold ${currentStyle.text}`}>{block.title}</h4>}
            <p className="text-xs text-slate-700 mt-1 leading-relaxed whitespace-pre-wrap">
              {renderTextWithDataChips(block.message || block.text || '')}
            </p>
            {block.action && (
              <button
                type="button"
                onClick={() => handleSend(block.action.message || block.action.action || block.action.label)}
                disabled={isTyping}
                className={`mt-2.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors shadow-2xs ${currentStyle.btn}`}
              >
                {block.action.label}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderResponseBlock = (block, messageId, index, precisionSubstituted = false, personalDataSubstituted = false) => {
    const blockId = `${messageId}-${block.blockId || index}`;
    const expandedStep = expandedProgressSteps[blockId] ?? 1;
    return (
      <div key={blockId} className={index > 0 ? 'mt-5' : ''}>
        <div className="mb-1.5 ml-1 flex items-end gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-400">
          <span>{t(preferences.language, "chat.botName")}</span>
          {index === 0 && precisionSubstituted && renderPrecisionBadge()}
          {index === 0 && personalDataSubstituted && renderDataProtectedBadge()}
        </div>
        {block.type === 'text' && (
          <div className="max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white p-3.5 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {block.contextText && (
              <div className="mb-2 text-slate-600">{renderTextWithDataChips(block.contextText)}</div>
            )}
            {renderTextWithDataChips(block.text)}
          </div>
        )}
        {block.type === 'clarification' && renderClarificationCard(block.text, block.options)}
        {block.type === 'stepper' && block.contextText && (
          <div className="mb-2 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {renderTextWithDataChips(block.contextText)}
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
            {renderTextWithDataChips(block.listIntro)}
          </div>
        )}
        {block.type === 'checklist' && renderChecklist(block.items, block.listTitle, block.options)}
        {block.type === 'comparison' && renderComparisonTable(block)}
        {block.type === 'accordion' && renderAccordionFaq(block, blockId)}
        {block.type === 'timeline' && renderTimelineTracker(block)}
        {block.type === 'carousel' && renderHorizontalCarousel(block)}
        {block.type === 'alert' && renderActionableAlert(block)}
        {block.type === 'escalation_confirmation' && renderEscalationConfirmation(block)}
        {block.extendedText && (
          <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
            {renderTextWithDataChips(block.extendedText)}
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

  // Interactive Embedded Service Request Block (rendered directly in chat flow)
  const renderEmbeddedServiceRequestField = (field, specialId, values) => {
    const value = values?.[field.field_id];
    if (field.field_type === "text") {
      return (
        <input
          type="text"
          className="w-full border border-slate-200 rounded-xl px-3.5 py-2.5 text-[15px] text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 bg-slate-50/70 focus:bg-white transition-all"
          placeholder={field.placeholder || ""}
          value={value ?? field.default ?? ""}
          onChange={(e) => updateEmbeddedFormField(specialId, field.field_id, e.target.value)}
        />
      );
    }
    if (field.field_type === "options") {
      return (
        <select
          className="w-full border border-slate-200 rounded-xl px-3.5 py-2.5 text-[15px] text-slate-800 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
          value={value ?? field.default ?? ""}
          onChange={(e) => updateEmbeddedFormField(specialId, field.field_id, e.target.value)}
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
        <label className="flex items-center gap-2.5 text-[14px] text-slate-700 cursor-pointer pt-0.5">
          <input
            type="checkbox"
            className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            checked={Boolean(value ?? field.default ?? false)}
            onChange={(e) => updateEmbeddedFormField(specialId, field.field_id, e.target.checked)}
          />
          <span className="leading-snug">{field.label}</span>
        </label>
      );
    }
    if (field.field_type === "document_reference") {
      const uploaded = Boolean(value);
      return (
        <div className="flex flex-col gap-1.5">
          <button
            type="button"
            onClick={() => updateEmbeddedFormField(specialId, field.field_id, `mock-doc-ref-${Date.now()}`)}
            className={`flex items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-[14px] font-medium transition-colors ${
              uploaded
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
            }`}
          >
            {uploaded ? <CheckCircle2 size={16} /> : <FileText size={16} />}
            <span>{uploaded ? t(preferences.language, "serviceRequest.uploaded") : t(preferences.language, "serviceRequest.simulateUpload")}</span>
          </button>
          <span className="text-[12px] text-slate-400">{t(preferences.language, "serviceRequest.mockDemoNotice")}</span>
        </div>
      );
    }
    return null;
  };

  const renderEmbeddedServiceRequest = (action) => {
    const specialId = action.specialId;
    const formState = embeddedFormStates[specialId] || {
      stepIndex: 0,
      values: {},
      status: 'pending',
      isSubmitting: false,
      errorMessage: ''
    };

    const steps = action.formSchema?.steps || formState.formSchema?.steps || [];
    const stepIndex = formState.stepIndex || 0;
    const currentStep = steps[stepIndex] || steps[0] || { caption: action.label, fields: [] };
    const isLastStep = stepIndex >= steps.length - 1;
    const isFirstStep = stepIndex === 0;

    // 1. COMPLETED STATE
    if (formState.status === 'completed') {
      return (
        <div id={`service-request-card-${specialId}`} className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-emerald-200 bg-emerald-50/70 p-4 shadow-sm animate-in fade-in duration-200 transition-all">
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-emerald-100 p-2 text-emerald-600 shrink-0 mt-0.5">
              <CheckCircle2 size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <h4 className="font-bold text-[15px] text-emerald-900 truncate">{action.label || t(preferences.language, "serviceRequest.statusCompleted")}</h4>
                <span className="inline-flex items-center rounded-full bg-emerald-100 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-800">
                  {t(preferences.language, "serviceRequest.statusCompleted")}
                </span>
              </div>
              <p className="mt-1 text-[14px] text-emerald-700 leading-relaxed">
                {t(preferences.language, "serviceRequest.submittedSuccess")}
              </p>
            </div>
          </div>
        </div>
      );
    }

    // 2. DENIED CREDENTIAL CHECK STATE
    if (formState.status === 'denied') {
      return (
        <div id={`service-request-card-${specialId}`} className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-amber-200 bg-amber-50/70 p-4 shadow-sm transition-all">
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-amber-100 p-2 text-amber-600 shrink-0 mt-0.5">
              <AlertCircle size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <h4 className="font-bold text-[15px] text-amber-900">{action.label}</h4>
                <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-semibold text-amber-800">
                  Verification Denied
                </span>
              </div>
              <p className="mt-1 text-[14px] text-amber-700 leading-relaxed">
                Security authorization was cancelled. You can retry anytime.
              </p>
              <button
                type="button"
                onClick={() => setEmbeddedFormStates(prev => ({ ...prev, [specialId]: { ...formState, status: 'pending' } }))}
                className="mt-2 text-[13px] font-semibold text-amber-900 hover:underline"
              >
                Retry
              </button>
            </div>
          </div>
        </div>
      );
    }

    // 3. CREDENTIAL CHECK REQUIRED (INLINE STEP-UP)
    if (formState.status === 'credential_check_required') {
      return (
        <div id={`service-request-card-${specialId}`} className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-blue-200 bg-white p-4 shadow-sm animate-in fade-in duration-200 transition-all">
          <div className="flex items-center gap-2.5 pb-3 border-b border-slate-100">
            <div className="rounded-xl bg-blue-50 p-2 text-blue-600">
              <ShieldCheck size={18} />
            </div>
            <div>
              <h4 className="font-bold text-[15px] text-slate-800">{t(preferences.language, "serviceRequest.credentialCheckTitle")}</h4>
              <p className="text-[11px] text-slate-400">Step-up Authentication</p>
            </div>
          </div>
          <p className="py-3.5 text-[14px] text-slate-600 leading-relaxed">
            {t(preferences.language, "serviceRequest.credentialCheckBody")}
          </p>
          <div className="flex flex-col gap-2.5 pt-2 border-t border-slate-100">
            <button
              type="button"
              onClick={() => resolveEmbeddedCredentialCheck(specialId, true)}
              disabled={formState.isSubmitting}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-4 rounded-xl text-[14px] transition-colors disabled:opacity-50"
            >
              {formState.isSubmitting && <Loader2 size={15} className="animate-spin" />}
              <span>{t(preferences.language, "serviceRequest.simulatePass")}</span>
            </button>
            <button
              type="button"
              onClick={() => resolveEmbeddedCredentialCheck(specialId, false)}
              disabled={formState.isSubmitting}
              className="w-full border border-slate-200 text-slate-600 hover:bg-slate-50 font-medium py-2.5 px-4 rounded-xl text-[14px] transition-colors disabled:opacity-50"
            >
              {t(preferences.language, "serviceRequest.simulateFail")}
            </button>
          </div>
        </div>
      );
    }

    // 4. ACTIVE EMBEDDED FORM
    return (
      <div id={`service-request-card-${specialId}`} className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-blue-100 bg-white shadow-sm overflow-hidden animate-in fade-in duration-150 transition-all">
        {/* Header */}
        <div className="px-4 py-3.5 bg-gradient-to-r from-blue-50/80 to-slate-50 border-b border-blue-100/80 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="rounded-xl bg-blue-600 text-white p-2 shrink-0">
              <FileText size={16} />
            </div>
            <div className="min-w-0">
              <h4 className="font-bold text-[15px] text-slate-800 truncate">
                {action.label || action.action_label || t(preferences.language, "serviceRequest.completeForm")}
              </h4>
              {action.description && (
                <p className="text-[13px] text-slate-500 line-clamp-2 leading-tight mt-0.5">{action.description}</p>
              )}
            </div>
          </div>
          {steps.length > 1 && (
            <span className="shrink-0 text-[11px] font-semibold text-blue-700 bg-blue-100/80 px-2.5 py-0.5 rounded-full">
              {stepIndex + 1}/{steps.length}
            </span>
          )}
        </div>

        {/* Step Progress Bar & Subtitle */}
        {steps.length > 1 && (
          <div className="bg-slate-50/80 border-b border-slate-100 px-4 py-2 flex items-center justify-between">
            <span className="text-[12px] font-medium text-slate-500">
              {currentStep.caption || `${t(preferences.language, "serviceRequest.step")} ${stepIndex + 1}`}
            </span>
            <div className="w-20 bg-slate-200 rounded-full h-1 overflow-hidden">
              <div
                className="bg-blue-600 h-1 transition-all duration-300 rounded-full"
                style={{ width: `${((stepIndex + 1) / steps.length) * 100}%` }}
              />
            </div>
          </div>
        )}

        {/* Fields */}
        <div className="p-4 space-y-3.5">
          {(currentStep.fields || []).map((field) => (
            <div key={field.field_id} className="space-y-1.5">
              {field.field_type !== "checkbox" && (
                <label className="block text-[13px] font-semibold text-slate-700 tracking-normal">
                  {field.label}
                </label>
              )}
              {renderEmbeddedServiceRequestField(field, specialId, formState.values)}
              {field.notice && (
                <p className="text-[12px] text-slate-400 italic">{field.notice}</p>
              )}
            </div>
          ))}

          {formState.errorMessage && (
            <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-red-700 text-[13px] flex items-center gap-2">
              <AlertCircle size={15} className="shrink-0" />
              <span>{formState.errorMessage}</span>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="px-4 py-3 bg-slate-50 border-t border-slate-100 flex items-center justify-between gap-2">
          {!isFirstStep ? (
            <button
              type="button"
              onClick={() => goToEmbeddedFormStep(specialId, -1, steps.length)}
              disabled={formState.isSubmitting}
              className="px-3.5 py-2 rounded-xl text-[14px] font-medium text-slate-600 hover:bg-slate-200/70 transition-colors disabled:opacity-40"
            >
              {t(preferences.language, "serviceRequest.back")}
            </button>
          ) : (
            <div />
          )}

          {isLastStep ? (
            <button
              type="button"
              onClick={() => submitEmbeddedForm(action)}
              disabled={formState.isSubmitting}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-xl text-[14px] transition-colors shadow-2xs disabled:opacity-50"
            >
              {formState.isSubmitting && <Loader2 size={14} className="animate-spin shrink-0" />}
              <span>{formState.isSubmitting ? t(preferences.language, "serviceRequest.submitting") : t(preferences.language, "serviceRequest.submit")}</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => goToEmbeddedFormStep(specialId, 1, steps.length)}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-xl text-[14px] transition-colors shadow-2xs"
            >
              <span>{t(preferences.language, "serviceRequest.next")}</span>
              <ChevronRight size={15} />
            </button>
          )}
        </div>
      </div>
    );
  };

  // The button attached to a message that resolved a deeplink navigation
  // target. destinationId/route/label all came straight from the backend's
  // ui_action_payload.navigationActions -- node_07 builds this list straight
  // from tool_results, unconditionally, the same way serviceRequestActions
  // is built; it never depends on anything the LLM generated.
  const renderNavigationAction = (action) => (
    <button
      type="button"
      onClick={() => setActiveDeeplink(action)}
      disabled={isTyping || isCompactingMemory}
      className="mt-3 flex w-full max-w-sm items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm font-semibold text-blue-700 shadow-sm transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <LayoutDashboard size={16} />
      {action.label}
    </button>
  );

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

      case 'profile-personal-details':
        return (
          <div className="w-full text-left space-y-4 max-w-md pb-6">
            {/* Profile Avatar Card */}
            <div className="bg-gradient-to-br from-blue-600 to-indigo-700 rounded-2xl p-5 text-white shadow-md relative overflow-hidden">
              <div className="absolute -right-6 -bottom-6 w-32 h-32 bg-white/10 rounded-full blur-2xl pointer-events-none" />
              <div className="flex items-center gap-4 relative z-10">
                <div className="relative">
                  <div className="w-14 h-14 rounded-2xl bg-white/20 backdrop-blur-md border border-white/30 flex items-center justify-center text-white text-xl font-bold shadow-inner">
                    {preferences.nickname ? preferences.nickname.slice(0, 2).toUpperCase() : 'BW'}
                  </div>
                  <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full bg-emerald-500 border-2 border-white flex items-center justify-center">
                    <Check size={11} className="text-white stroke-[3]" />
                  </div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-base text-white truncate">
                      {preferences.nickname ? `${preferences.nickname} (Mr. Wu)` : 'Benson Wu'}
                    </h3>
                  </div>
                  <p className="text-xs text-blue-100 font-mono mt-0.5">ID: USR-999 • Priority Banking</p>
                  <div className="mt-2 inline-flex items-center gap-1 bg-white/15 backdrop-blur-xs px-2.5 py-0.5 rounded-full text-[10px] font-medium text-blue-50 border border-white/20">
                    <ShieldCheck size={11} className="text-emerald-300" />
                    Singpass MyInfo Verified
                  </div>
                </div>
              </div>
            </div>

            {/* Personal Details Section */}
            <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3.5">
              <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider">Legal Identification</h4>
                <span className="text-[10px] text-slate-400 font-medium">Synced with GovTech</span>
              </div>
              
              <div className="space-y-3">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Full Legal Name</label>
                  <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-700 flex items-center justify-between">
                    <span>Benson Qianzong Wu</span>
                    <LockIcon size={13} className="text-slate-400" />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">NRIC / FIN</label>
                    <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-700 font-mono">
                      S••••789A
                    </div>
                  </div>
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Date of Birth</label>
                    <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-700">
                      15 Oct 1990
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Nationality & Tax Residency</label>
                  <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-700 flex items-center justify-between">
                    <span>Singaporean (SG)</span>
                    <span className="text-[11px] text-emerald-600 font-medium bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-100">Tax Resident</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Contact Details Section */}
            <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3.5">
              <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider">Contact Information</h4>
                <span className="text-[10px] text-blue-600 font-medium">Synced Profile</span>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Registered Mobile Number</label>
                  <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-800 flex items-center justify-between">
                    <span className="font-mono">+65 9123 4567</span>
                    <span className="text-[10px] text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full font-semibold border border-emerald-200">Verified</span>
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Email Address</label>
                  <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-800 flex items-center justify-between">
                    <span className="font-mono text-xs">benson.wu@example.com</span>
                    <span className="text-[10px] text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full font-semibold border border-emerald-200">Verified</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Address Section */}
            <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3.5">
              <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider">Registered Address</h4>
                <span className="text-[10px] text-slate-400">Primary Residence</span>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Residential Address</label>
                <div className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-sm text-slate-800 leading-relaxed">
                  12 Marina Boulevard, #28-01<br />
                  Marina Bay Financial Centre Tower 3<br />
                  Singapore 018982
                </div>
              </div>

              <div className="pt-1 flex items-center justify-between text-xs text-slate-600 border-t border-slate-100">
                <span>Mailing Address same as residential</span>
                <div className="w-9 h-5 bg-blue-600 rounded-full relative pointer-events-none">
                  <div className="w-3.5 h-3.5 bg-white rounded-full absolute right-1 top-0.75 shadow-xs" />
                </div>
              </div>
            </div>

            {/* Mock Action Buttons & Notice */}
            <div className="pt-2 space-y-2">
              <button
                type="button"
                disabled
                className="w-full bg-slate-300 text-white font-semibold py-3.5 rounded-xl text-sm transition-all cursor-not-allowed shadow-xs"
              >
                Save Changes (Read-Only Demo)
              </button>
              <p className="text-[11px] text-slate-400 text-center leading-relaxed">
                🔒 For regulatory security, live personal updates require official Singpass authentication or the in-chat Change Address service request.
              </p>
            </div>
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
      return { label: t(preferences.language, 'serviceRequest.statusPending'), icon: <Clock size={13} />, className: 'text-blue-700 bg-blue-50 border-blue-200' };
    }
    if (status === 'completed') {
      return { label: t(preferences.language, 'serviceRequest.statusCompleted'), icon: <CheckCircle2 size={13} />, className: 'text-emerald-700 bg-emerald-50 border-emerald-200' };
    }
    return { label: t(preferences.language, 'serviceRequest.statusExpired'), icon: <XCircle size={13} />, className: 'text-slate-500 bg-slate-50 border-slate-200' };
  };

  const renderServiceRequestListModal = () => null;

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

  // Tokens are dynamically rendered into interactive protected / expired chips
  // via renderTextWithDataChips using personalDataStore and preferences.language.
  const resolvedMessages = messages;

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4 font-sans">
      
      {/* Mobile Device Mockup */}
      <div className="w-full max-w-md bg-white rounded-[2.5rem] shadow-2xl overflow-hidden flex flex-col h-[85vh] max-h-[850px] border-[6px] border-slate-800 relative">
        
        {/* Dynamic User Identity Header */}
        <div className="bg-white border-b border-slate-100 p-4 pb-3 flex flex-col gap-3 z-10 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="relative bg-gradient-to-tr from-blue-700 via-blue-600 to-indigo-600 w-11 h-11 rounded-2xl flex items-center justify-center shadow-md shadow-blue-500/20 border border-white/20">
                <Landmark className="text-white drop-shadow-xs" size={22} strokeWidth={2.2} />
                <div className="absolute -bottom-0.5 -right-0.5 bg-indigo-500 rounded-full p-0.5 border-2 border-white shadow-xs">
                  <Sparkles size={8} className="text-white" />
                </div>
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
                <button onClick={() => setShowPreferences(true)} className="p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors" title={t(preferences.language, "header.preferences")}>
                  <Settings size={18}/>
                </button>
              )}
              <button onClick={handleLoginToggle} className="p-2 text-slate-400 hover:bg-slate-100 rounded-full transition-colors" title={t(preferences.language, "header.toggleUser")}>
                {isLoggedIn ? <LogOut size={18}/> : <LogIn size={18}/>}
              </button>
            </div>
          </div>

          {/* User Status Bar with Integrated Service Request Binder Drawer on Hover */}
          <div
            className="relative z-30"
            onMouseEnter={handleBannerMouseEnter}
            onMouseLeave={handleBannerMouseLeave}
          >
            <div className={`flex items-center justify-between px-3 py-2 transition-all select-none ${
              isBannerHovered && isLoggedIn
                ? 'bg-blue-50/95 border-x border-t border-blue-200 shadow-sm rounded-t-xl rounded-b-none'
                : (isLoggedIn ? 'bg-blue-50/50 border border-blue-100 hover:bg-blue-50 hover:border-blue-200 rounded-xl cursor-pointer' : 'bg-slate-50 border border-slate-100 rounded-xl')
            }`}>
              {/* Left: User Identity & Masked Account */}
              <div className="flex items-center gap-2.5">
                {isLoggedIn ? (
                  <>
                    <div className="flex items-center gap-1.5">
                      <div className="w-5 h-5 rounded-full bg-blue-100/90 flex items-center justify-center border border-blue-200/60 shrink-0">
                        <UserCheck size={12} className="text-blue-700"/>
                      </div>
                      <span className="text-xs font-semibold text-slate-800">{preferences.nickname || MOCK_PROFILE.displayName}</span>
                    </div>
                    <span className="text-[10px] font-mono text-slate-500 bg-white/90 px-2 py-0.5 rounded-md border border-slate-200/80 shadow-2xs">
                      {t(preferences.language, "misc.maskedAcc")}
                    </span>
                  </>
                ) : (
                  <>
                    <AlertCircle size={14} className="text-slate-500 shrink-0"/>
                    <span className="text-xs font-medium text-slate-600">{t(preferences.language, "misc.guestUser")}</span>
                  </>
                )}
              </div>

              {/* Right: Service Request Status & Drawer Trigger */}
              <div className="flex items-center gap-2">
                {isLoggedIn && (
                  <>
                    {(() => {
                      const pendingCount = serviceRequestList.filter(item => PENDING_SERVICE_REQUEST_STATUSES.includes(item.status)).length;
                      if (pendingCount > 0) {
                        return (
                          <span className="inline-flex items-center gap-1.5 bg-white border border-blue-200 text-blue-900 text-[10px] font-semibold rounded-full px-2.5 py-0.5 shadow-2xs">
                            <span className="w-1.5 h-1.5 rounded-full bg-blue-600 animate-pulse shrink-0" />
                            <span className="font-bold text-blue-950">{pendingCount}</span>
                            <span className="text-[9.5px] font-medium text-blue-600">{t(preferences.language, 'serviceRequest.statusPending').toLowerCase()}</span>
                          </span>
                        );
                      }
                      return (
                        <span className="text-[10px] font-medium text-slate-400">
                          {t(preferences.language, 'serviceRequest.listTitle')}
                        </span>
                      );
                    })()}
                    <ChevronDown size={14} className={`text-slate-400 transition-transform duration-200 ${isBannerHovered ? 'rotate-180 text-blue-600' : ''}`} />
                  </>
                )}
              </div>
            </div>

            {/* Binder-Style Service Form Drawer */}
            {isLoggedIn && isBannerHovered && (
              <div
                className="absolute top-full left-0 right-0 bg-white/98 backdrop-blur-xl rounded-b-2xl border-x border-b border-blue-200 shadow-2xl z-40 overflow-hidden animate-in fade-in slide-in-from-top-1 duration-150"
                onMouseEnter={handleBannerMouseEnter}
                onMouseLeave={handleBannerMouseLeave}
              >
                {/* Binder Header with 3 Status Index Tabs */}
                <div className="bg-slate-100/90 border-b border-slate-200 px-3 pt-2.5 flex items-end justify-between gap-1">
                  <div className="flex items-end gap-1">
                    {/* Pending Tab */}
                    <button
                      type="button"
                      onClick={() => { setServiceRequestTab('pending'); setServiceRequestPage(1); }}
                      className={`px-2.5 py-1.5 rounded-t-lg text-[11px] font-bold transition-all flex items-center gap-1.5 border-t-2 border-x ${
                        serviceRequestTab === 'pending'
                          ? 'bg-white text-blue-900 border-t-blue-600 border-x-slate-200 -mb-px shadow-2xs'
                          : 'bg-slate-200/60 text-slate-500 hover:text-slate-700 border-t-transparent border-x-transparent hover:bg-slate-200'
                      }`}
                    >
                      <Clock size={12} className={serviceRequestTab === 'pending' ? 'text-blue-600' : 'text-slate-400'} />
                      <span>{t(preferences.language, 'serviceRequest.statusPending')}</span>
                      <span className={`text-[9px] px-1.5 py-0.2 rounded-full font-bold ${
                        serviceRequestTab === 'pending' ? 'bg-blue-100 text-blue-800' : 'bg-slate-300/70 text-slate-600'
                      }`}>
                        {serviceRequestList.filter(item => PENDING_SERVICE_REQUEST_STATUSES.includes(item.status)).length}
                      </span>
                    </button>

                    {/* Completed Tab */}
                    <button
                      type="button"
                      onClick={() => { setServiceRequestTab('completed'); setServiceRequestPage(1); }}
                      className={`px-2.5 py-1.5 rounded-t-lg text-[11px] font-bold transition-all flex items-center gap-1.5 border-t-2 border-x ${
                        serviceRequestTab === 'completed'
                          ? 'bg-white text-emerald-800 border-t-emerald-500 border-x-slate-200 -mb-px shadow-2xs'
                          : 'bg-slate-200/60 text-slate-500 hover:text-slate-700 border-t-transparent border-x-transparent hover:bg-slate-200'
                      }`}
                    >
                      <CheckCircle2 size={12} className={serviceRequestTab === 'completed' ? 'text-emerald-600' : 'text-slate-400'} />
                      <span>{t(preferences.language, 'serviceRequest.statusCompleted')}</span>
                      <span className={`text-[9px] px-1.5 py-0.2 rounded-full font-bold ${
                        serviceRequestTab === 'completed' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-300/70 text-slate-600'
                      }`}>
                        {serviceRequestList.filter(item => item.status === 'completed').length}
                      </span>
                    </button>

                    {/* Expired Tab */}
                    <button
                      type="button"
                      onClick={() => { setServiceRequestTab('expired'); setServiceRequestPage(1); }}
                      className={`px-2.5 py-1.5 rounded-t-lg text-[11px] font-bold transition-all flex items-center gap-1.5 border-t-2 border-x ${
                        serviceRequestTab === 'expired'
                          ? 'bg-white text-slate-700 border-t-slate-400 border-x-slate-200 -mb-px shadow-2xs'
                          : 'bg-slate-200/60 text-slate-500 hover:text-slate-700 border-t-transparent border-x-transparent hover:bg-slate-200'
                      }`}
                    >
                      <XCircle size={12} className={serviceRequestTab === 'expired' ? 'text-slate-500' : 'text-slate-400'} />
                      <span>{t(preferences.language, 'serviceRequest.statusExpired')}</span>
                      <span className={`text-[9px] px-1.5 py-0.2 rounded-full font-bold ${
                        serviceRequestTab === 'expired' ? 'bg-slate-200 text-slate-700' : 'bg-slate-300/70 text-slate-600'
                      }`}>
                        {serviceRequestList.filter(item => item.status === 'expired').length}
                      </span>
                    </button>
                  </div>
                </div>

                {/* Binder Content Body */}
                <div className="p-2.5 max-h-[260px] overflow-y-auto bg-white space-y-1.5">
                  {(() => {
                    const filteredList = serviceRequestList.filter(item => {
                      if (serviceRequestTab === 'pending') return PENDING_SERVICE_REQUEST_STATUSES.includes(item.status);
                      if (serviceRequestTab === 'completed') return item.status === 'completed';
                      if (serviceRequestTab === 'expired') return item.status === 'expired';
                      return false;
                    });

                    if (filteredList.length === 0) {
                      return (
                        <div className="py-6 text-center text-slate-400 flex flex-col items-center gap-1.5">
                          <ClipboardList size={22} className="text-slate-300" />
                          <p className="text-xs">{t(preferences.language, 'serviceRequest.listEmpty')}</p>
                        </div>
                      );
                    }

                    return filteredList.map((item) => {
                      const meta = serviceRequestStatusMeta(item.status);
                      const isPending = PENDING_SERVICE_REQUEST_STATUSES.includes(item.status);

                      return (
                        <button
                          key={item.special_id}
                          type="button"
                          onClick={async () => {
                            setIsBannerHovered(false);
                            const cardElement = document.getElementById(`service-request-card-${item.special_id}`);
                            if (cardElement) {
                              cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                              cardElement.classList.add('ring-2', 'ring-blue-500', 'shadow-md');
                              setTimeout(() => {
                                cardElement.classList.remove('ring-2', 'ring-blue-500', 'shadow-md');
                              }, 2000);
                            } else {
                              try {
                                const res = await fetch(`http://localhost:8000/api/service-request/${item.special_id}`);
                                const data = await res.json();
                                if (data.status === 'pending') {
                                  setMessages(prev => [...prev, {
                                    id: Date.now(),
                                    sender: 'bot',
                                    text: item.description || item.action_label || t(preferences.language, "serviceRequest.completeForm"),
                                    type: 'text',
                                    serviceRequestActions: [{
                                      specialId: item.special_id,
                                      serviceRequestId: data.service_request_id,
                                      label: item.action_label || data.description,
                                      description: data.description || item.description,
                                      formSchema: data.form_schema
                                    }],
                                    createdAt: new Date().toISOString()
                                  }]);
                                  shouldAutoScrollRef.current = true;
                                } else if (data.status === 'completed') {
                                  setServiceRequestNotice(t(preferences.language, 'serviceRequest.alreadyCompleted'));
                                } else if (data.status === 'expired') {
                                  setServiceRequestNotice(t(preferences.language, 'serviceRequest.expired'));
                                }
                              } catch (e) {
                                console.error('Failed to load service request from drawer', e);
                              }
                            }
                          }}
                          className={`w-full text-left p-2.5 rounded-xl border transition-all flex flex-col gap-1 ${
                            isPending
                              ? 'border-blue-200/80 bg-blue-50/40 hover:bg-blue-50 hover:border-blue-300 cursor-pointer shadow-2xs'
                              : 'border-slate-200/70 bg-slate-50/60 hover:bg-slate-100 cursor-pointer'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-bold text-slate-800 line-clamp-1">{item.action_label || item.description}</span>
                            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-bold shrink-0 ${meta.className}`}>
                              {meta.icon}
                              {meta.label}
                            </span>
                          </div>
                          {item.action_label && item.description && (
                            <p className="text-[11px] text-slate-600 leading-snug line-clamp-1">{item.description}</p>
                          )}
                          <div className="flex items-center justify-between pt-0.5 text-[9px] text-slate-400 font-mono">
                            <span>ID: {item.special_id?.substring(0, 10)}...</span>
                            {item.created_at && (
                              <span>{new Date(item.created_at).toLocaleDateString()}</span>
                            )}
                          </div>
                        </button>
                      );
                    });
                  })()}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Chat Conversation Area */}
        <div
          ref={chatContainerRef}
          onScroll={handleHistoryScroll}
          className="flex-1 overflow-y-auto p-4 space-y-5 bg-slate-50/50"
        >


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

          {resolvedMessages.map((msg, index) => {
            const timestamp = msg.createdAt || new Date().toISOString();
            const dateLabel = formatMessageDate(timestamp);
            const previousDateLabel = index > 0
              ? formatMessageDate(resolvedMessages[index - 1].createdAt || timestamp)
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
                        {msg.personalDataSubstituted && renderDataProtectedBadge()}
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
                          {msg.sender === 'bot' ? renderTextWithDataChips(msg.text) : msg.text}
                        </div>
                      </>
                    )}

                    {/* Dynamic UI Components */}
                    {msg.type === 'carousel' && renderCarousel(msg.options)}
                    {msg.type === 'blocks' && msg.responseBlocks?.map((block, blockIndex) =>
                      renderResponseBlock(block, msg.id, blockIndex, msg.precisionSubstituted, msg.personalDataSubstituted)
                    )}
                    {msg.serviceRequestActions?.map((action) => (
                      <React.Fragment key={action.specialId}>
                        {renderEmbeddedServiceRequest(action)}
                      </React.Fragment>
                    ))}
                    {msg.navigationActions?.map((action) => (
                      <React.Fragment key={action.destinationId}>
                        {renderNavigationAction(action)}
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
                        {renderTextWithDataChips(msg.extendedText)}
                      </div>
                    )}
                    {msg.type === 'stepper' && msg.clarificationText && (
                      renderClarificationCard(msg.clarificationText)
                    )}
                    {msg.type === 'checklist' && msg.listIntro && (
                      <div className="mt-2 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {renderTextWithDataChips(msg.listIntro)}
                      </div>
                    )}
                    {msg.type === 'checklist' && renderChecklist(msg.items, msg.listTitle, msg.options)}
                    {msg.type === 'checklist' && msg.extendedText && (
                      <div className="mt-3 max-w-sm rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-3.5 py-3 text-[15px] leading-relaxed text-slate-700 whitespace-pre-wrap shadow-sm">
                        {renderTextWithDataChips(msg.extendedText)}
                      </div>
                    )}
                    {msg.type === 'checklist' && msg.listClarificationText && renderClarificationCard(msg.listClarificationText)}
                    {msg.type === 'escalation' && renderEscalation()}
                    
                    {/* Message Footer: Timestamp on left, Thumbs feedback on right */}
                    <div className={`mt-1 flex items-center ${msg.sender === 'user' ? 'justify-end mr-1' : 'justify-between ml-1 mr-1'}`}>
                      <span className="text-[10px] tabular-nums text-slate-400">
                        {formatMessageTime(timestamp)}
                      </span>
                      {msg.sender === 'bot' && renderTurnFeedback(msg)}
                    </div>
                    
                    {/* Quick action chips placed below timestamp & thumbs */}
                    {msg.type === 'chips' && renderChips(msg.options)}
                    {msg.type === 'blocks' && msg.options?.length > 0 && renderChips(msg.options)}
                  </div>
                </div>
              </React.Fragment>
            );
          })}
          
          {/* 3. UI RENDERING REQUIREMENT: Agentic Processing Tracker */}
          {isTyping && (
            <div className="flex flex-col items-start gap-1.5">
              {taskConfidence !== null && (
                <div className="flex items-center gap-1 pl-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  <span>{t(preferences.language, "caption.confidence")}: {Math.round(taskConfidence * 100)}%</span>
                </div>
              )}
              {isLowConfidenceMode && (
                <div className="flex items-center gap-1 pl-1 text-[11px] font-semibold uppercase tracking-wide text-amber-600">
                  <Sparkles size={11} className="shrink-0" />
                  <span>{t(preferences.language, "caption.thinkingHarder")}</span>
                </div>
              )}
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

        {/* Text Input Area with Floating Suggestions on Hover */}
        <div
          className="relative bg-white border-t border-slate-200 p-3 z-10 shadow-[0_-4px_10px_rgba(0,0,0,0.02)]"
          onMouseEnter={handleInputMouseEnter}
          onMouseLeave={handleInputMouseLeave}
        >
          {/* Soft-escalation banner: reflects only the LATEST turn's own
              signal (softEscalationAvailable), never a chip baked into a
              past message -- it disappears on its own the moment a turn
              comes back without the signal, instead of lingering in chat
              history forever. */}
          {softEscalationAvailable && (
            <div className="mb-2.5 flex items-center gap-2.5 rounded-xl border border-blue-100 bg-blue-50 px-3.5 py-2.5">
              <UserCheck size={16} className="shrink-0 text-blue-600" aria-hidden="true" />
              <span className="flex-1 text-[13px] font-medium text-blue-800">
                {t(preferences.language, "misc.softEscalationBanner")}
              </span>
              <button
                type="button"
                onClick={() => handleSend("Talk to Agent")}
                disabled={isTyping || isCompactingMemory}
                className="shrink-0 rounded-full bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-700 active:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
              >
                {t(preferences.language, "misc.softEscalationAction")}
              </button>
            </div>
          )}

          {/* Next-action suggestions & Input Capsule: seamlessly unified */}
          {(() => {
            const showNextActions = (isInputHovered || isInputFocused) && !isTyping && isLoggedIn && !activeDeeplink && nextActions.length > 0;
            return (
              <div
                className={`relative rounded-2xl transition-all ${
                  showNextActions
                    ? 'bg-white shadow-[0_4px_24px_rgba(0,0,0,0.06)] border border-slate-200 focus-within:ring-2 focus-within:ring-blue-500/40 focus-within:border-blue-300'
                    : 'bg-slate-100 border border-transparent focus-within:ring-2 focus-within:ring-blue-500/40 focus-within:bg-white focus-within:border-blue-200'
                }`}
                onMouseEnter={handleInputMouseEnter}
                onMouseLeave={handleInputMouseLeave}
              >
                {showNextActions && (
                  <div className="p-2.5 pb-2 border-b border-slate-100 flex flex-col gap-1 animate-in fade-in duration-150">
                    <div className="px-2 pt-0.5 pb-1 flex items-center justify-between text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
                      <span>{t(preferences.language, "chat.suggestedFollowUps")}</span>
                    </div>
                    {nextActions.map((action, idx) => (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => handleSend(action)}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          handleSend(action);
                        }}
                        className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-xl text-[13px] font-medium text-slate-800 hover:text-blue-700 hover:bg-slate-50 active:bg-slate-100 transition-all text-left group cursor-pointer border border-transparent hover:border-slate-200/60"
                      >
                        <span className="flex-1 whitespace-normal break-words leading-snug">{action}</span>
                        <ArrowRight size={13} className="text-slate-300 group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all shrink-0 mt-0.5" />
                      </button>
                    ))}
                  </div>
                )}

                <div className="relative flex items-end p-1.5">
                  <textarea
                    ref={inputRef}
                    rows={1}
                    maxLength={1000}
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    onFocus={() => setIsInputFocused(true)}
                    onBlur={() => setIsInputFocused(false)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        if (inputValue.trim() && !isTyping && isLoggedIn && !activeDeeplink) {
                          handleSend(inputValue);
                        }
                      }
                    }}
                    placeholder={t(preferences.language, "chat.placeholder")}
                    className="w-full bg-transparent text-slate-800 text-[15px] pl-3.5 pr-11 py-2 focus:outline-none resize-none leading-relaxed placeholder:text-slate-400 max-h-[140px] overflow-y-auto"
                    style={{ minHeight: '40px' }}
                    disabled={isTyping || !isLoggedIn || Boolean(activeDeeplink)}
                  />
                  <button
                    onClick={() => handleSend(inputValue)}
                    disabled={!inputValue.trim() || isTyping || !isLoggedIn || Boolean(activeDeeplink)}
                    className="absolute right-1.5 bottom-1.5 bg-blue-600 text-white w-9 h-9 rounded-xl flex items-center justify-center disabled:opacity-40 disabled:bg-slate-300 hover:bg-blue-700 transition-all shadow-xs shrink-0"
                    title="Send"
                  >
                    <Send size={16} className="ml-0.5" />
                  </button>
                </div>
              </div>
            );
          })()}
          <div className="flex items-center justify-center gap-1 mt-2.5 mb-0.5">
             <LockIcon size={10} className="text-slate-400"/>
             <span className="text-[10px] text-slate-400 font-medium tracking-wide uppercase">{t(preferences.language, "chat.footer")}</span>
          </div>
        </div>
        {renderPreferencesModal()}
        {renderDeeplinkMockScreen()}
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
