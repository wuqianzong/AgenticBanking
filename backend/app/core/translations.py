"""Deterministic, LLM-independent message strings for WP-4 style personalization.

These cover the fallback and rejection paths that must still respect a
customer's preferred language even when the LLM is bypassed entirely (e.g.
a Prompt Guard rejection or a deterministic clarification fallback). Mirrors
the language set and fallback behavior of frontend/src/translations.js.
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "English": {
        "greeting_new": "Welcome to Horizon! How can I help with your banking today?",
        "greeting_new_named": "Welcome to Horizon, {nickname}! How can I help with your banking today?",
        "greeting_returning": "Welcome back! How can I help with your banking today?",
        "greeting_returning_named": "Welcome back, {nickname}! How can I help with your banking today?",
        "non_financial_fallback": "I’m here if you have a banking question.",
        "non_financial_retry": "Thanks for sharing that. I’m here whenever you have a Horizon Bank question.",
        "esc_fallback_desc": "The current banking conversation.",
        "esc_user_asked": "You asked to continue with a human agent.",
        "esc_safety_retry": "The assistant could not safely complete the request after its validation checks.",
        "esc_default_review": "A human review has been requested for this conversation.",
        "error_malicious": "We cannot process your request. Please try rephrasing or ask another question.",
        "non_compliant": "I’m unable to help with that request.",
        "non_financial_rejection": "I’m Horizon, and I can help with Horizon Bank questions whenever you need.",
        "fail": "Sorry, I’m unable to respond to that right now. Please try again or rephrase your question.",
        "service_request_intro_single": "You can fulfil your request using the form below.",
        "service_request_intro_plural": "You can fulfil these requests using the forms below.",
    },
    "Mandarin": {
        "greeting_new": "欢迎来到 Horizon!请问今天有什么银行业务需要我协助的吗?",
        "greeting_new_named": "欢迎来到 Horizon,{nickname}!请问今天有什么银行业务需要我协助的吗?",
        "greeting_returning": "欢迎回来!请问今天有什么银行业务需要我协助的吗?",
        "greeting_returning_named": "欢迎回来,{nickname}!请问今天有什么银行业务需要我协助的吗?",
        "non_financial_fallback": "如果您有银行相关的问题,我随时为您解答。",
        "non_financial_retry": "感谢您的分享。如果您有 Horizon 银行相关的问题,我随时在这里为您解答。",
        "esc_fallback_desc": "当前的银行对话。",
        "esc_user_asked": "您已要求转接人工客服。",
        "esc_safety_retry": "经过安全验证后,助手无法安全地完成此请求。",
        "esc_default_review": "此对话已申请人工审核。",
        "error_malicious": "我们无法处理您的请求。请尝试重新表述或提出其他问题。",
        "non_compliant": "很抱歉,我无法协助处理该请求。",
        "non_financial_rejection": "我是 Horizon,随时为您解答 Horizon 银行相关的问题。",
        "fail": "抱歉,我目前无法回应。请重试或重新表述您的问题。",
        "service_request_intro_single": "您可以通过下方的表单完成您的请求。",
        "service_request_intro_plural": "您可以通过下方的表单完成这些请求。",
    },
    "Spanish": {
        "greeting_new": "¡Bienvenido a Horizon! ¿Cómo puedo ayudarle hoy con su banca?",
        "greeting_new_named": "¡Bienvenido a Horizon, {nickname}! ¿Cómo puedo ayudarle hoy con su banca?",
        "greeting_returning": "¡Bienvenido de nuevo! ¿Cómo puedo ayudarle hoy con su banca?",
        "greeting_returning_named": "¡Bienvenido de nuevo, {nickname}! ¿Cómo puedo ayudarle hoy con su banca?",
        "non_financial_fallback": "Estoy aquí si tiene alguna pregunta bancaria.",
        "non_financial_retry": "Gracias por compartir eso. Estoy aquí siempre que tenga una pregunta sobre Horizon Bank.",
        "esc_fallback_desc": "La conversación bancaria actual.",
        "esc_user_asked": "Usted solicitó continuar con un agente humano.",
        "esc_safety_retry": "El asistente no pudo completar la solicitud de forma segura tras sus verificaciones de validación.",
        "esc_default_review": "Se ha solicitado una revisión humana para esta conversación.",
        "error_malicious": "No podemos procesar su solicitud. Intente reformularla o haga otra pregunta.",
        "non_compliant": "No puedo ayudar con esa solicitud.",
        "non_financial_rejection": "Soy Horizon y puedo ayudarle con preguntas de Horizon Bank cuando lo necesite.",
        "fail": "Lo siento, no puedo responder en este momento. Intente nuevamente o reformule su pregunta.",
        "service_request_intro_single": "Puede completar su solicitud usando el formulario a continuación.",
        "service_request_intro_plural": "Puede completar estas solicitudes usando los formularios a continuación.",
    },
}


def get_message(language: str, key: str) -> str:
    """Return the deterministic message for `key`, falling back to English, then the raw key.

    Mirrors frontend/src/translations.js's `t(lang, key)` fallback order so
    an unrecognized language or key degrades the same way on both sides.
    """
    return TRANSLATIONS.get(language, {}).get(key) or TRANSLATIONS["English"].get(key) or key
