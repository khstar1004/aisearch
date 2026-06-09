(function () {
  const DEFAULTS = {
    target: "#haeorum-ai-search",
    mallId: "shop001",
    siteId: "",
    apiBaseUrl: "",
    apiKey: "",
    resultPageUrl: "",
    resultPageTarget: "_self",
    limit: 20,
    maxImageMb: 5,
    minImageDimension: 16,
    attachToSearchInput: "",
    attachAfterSelector: "",
    autoAttach: true,
    fallbackFloating: false,
    mountWaitMs: 3000,
    prefillFromSearchInput: true,
    triggerTitle: "AI 이미지검색",
    triggerAriaLabel: "AI 상품 검색",
    accentColor: "#ee1b24",
    accentTextColor: "#ffffff",
    accentSoftColor: "#fff3f0",
    zIndex: 2147483000
  };

  const state = {
    options: DEFAULTS,
    file: null,
    objectUrl: null,
    root: null,
    modal: null,
    searchInput: null,
    imageValidationPending: false,
    lastFocusedElement: null,
    lastQuery: "",
    lastCategory: "",
    lastQueryType: "",
    nextOffset: null,
    searchInFlight: false,
    deferredInitOptions: null,
    deferredInitScheduled: false,
    pendingMountOptions: null,
    pendingMountObserver: null,
    pendingMountTimer: null
  };

  const MALL_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?$/;

  function init(options) {
    const providedOptions = options || {};
    state.options = Object.assign({}, DEFAULTS, providedOptions);
    const mallId = normalizeMallIdentifier(providedOptions.mallId, "mallId");
    const siteId = normalizeMallIdentifier(providedOptions.siteId, "siteId");
    if (mallId && siteId && mallId !== siteId) {
      throw new Error("mallId and siteId must match when both are provided.");
    }
    if (mallId) {
      state.options.mallId = mallId;
    }
    if (siteId) {
      state.options.siteId = siteId;
    }
    if (!mallId && siteId) {
      state.options.mallId = siteId;
    }
    state.options.mallId = normalizeMallIdentifier(state.options.mallId, "mallId");
    state.options.siteId = normalizeMallIdentifier(state.options.siteId, "siteId");
    if (!state.options.mallId) {
      throw new Error("mallId or siteId is required.");
    }
    const apiBaseUrlOption = trimText(state.options.apiBaseUrl);
    const apiBaseUrl = normalizeApiBaseUrl(apiBaseUrlOption || inferApiBaseUrlFromScript());
    if (!apiBaseUrl) {
      throw new Error("apiBaseUrl must be an absolute HTTP(S) URL without credentials, query strings, fragments, whitespace, backslashes, or invalid ports, or the widget script must be loaded from an absolute HTTP(S) URL.");
    }
    state.options.apiBaseUrl = apiBaseUrl;
    const resultPageUrlOption = trimText(state.options.resultPageUrl);
    if (resultPageUrlOption) {
      const resultPageUrl = normalizeResultPageUrl(resultPageUrlOption);
      if (!resultPageUrl) {
        throw new Error("resultPageUrl must be an HTTP(S) URL or same-site relative URL without credentials, javascript URLs, whitespace, or backslashes.");
      }
      state.options.resultPageUrl = resultPageUrl;
    }
    state.options.resultPageTarget = normalizeResultPageTarget(state.options.resultPageTarget);
    const explicitTargetSelector = trimText(providedOptions.target);
    const explicitSearchInputSelector = trimText(providedOptions.attachToSearchInput);
    const explicitAttachAfterSelector = trimText(providedOptions.attachAfterSelector);
    const explicitMissingSelector = (
      (explicitTargetSelector && !findElement(explicitTargetSelector)) ||
      (explicitSearchInputSelector && !findElement(explicitSearchInputSelector)) ||
      (explicitAttachAfterSelector && !findElement(explicitAttachAfterSelector))
    );
    const mountTargets = resolveMountTargets(explicitMissingSelector);
    if (!mountTargets.target && !mountTargets.attachAnchor) {
      if (shouldWaitForDomReady()) {
        deferInitUntilReady(providedOptions);
        return;
      }
      if (shouldWaitForLateMountTargets()) {
        waitForLateMountTargets(providedOptions);
        return;
      }
      if (shouldMountFallbackFloating()) {
        mountFallbackFloating(providedOptions);
        return;
      }
      throw new Error("HaeorumAISearch target not found: " + state.options.target);
    }
    clearPendingMountWait();
    resetExistingWidget();
    injectStyles();
    state.searchInput = mountTargets.searchInput;
    state.root = mountRoot(mountTargets.target, mountTargets.attachAnchor, mountTargets.mode);
    applyDesignOptions(state.root);
    buildTrigger(state.root);
    buildModal();
  }

  function shouldWaitForDomReady() {
    return document.readyState === "loading" && typeof document.addEventListener === "function";
  }

  function deferInitUntilReady(options) {
    state.deferredInitOptions = Object.assign({}, options || {});
    if (state.deferredInitScheduled) {
      return;
    }
    state.deferredInitScheduled = true;
    document.addEventListener("DOMContentLoaded", function () {
      const pendingOptions = state.deferredInitOptions || {};
      state.deferredInitOptions = null;
      state.deferredInitScheduled = false;
      init(pendingOptions);
    }, { once: true });
  }

  function destroy() {
    clearPendingMountWait();
    resetExistingWidget();
    state.deferredInitOptions = null;
    state.deferredInitScheduled = false;
  }

  function shouldWaitForLateMountTargets() {
    return mountWaitMs() > 0 && document.body && Boolean(getMutationObserverConstructor());
  }

  function waitForLateMountTargets(options) {
    state.pendingMountOptions = Object.assign({}, options || {});
    if (state.pendingMountObserver) {
      return;
    }
    const Observer = getMutationObserverConstructor();
    if (!Observer) {
      throw new Error("HaeorumAISearch target not found: " + state.options.target);
    }
    state.pendingMountObserver = new Observer(function () {
      const pendingOptions = state.pendingMountOptions || {};
      if (!pendingMountTargetsReady(pendingOptions)) {
        return;
      }
      clearPendingMountWait();
      init(pendingOptions);
    });
    state.pendingMountObserver.observe(document.body, { childList: true, subtree: true });
    state.pendingMountTimer = window.setTimeout(function () {
      if (shouldMountFallbackFloating()) {
        mountFallbackFloating(state.pendingMountOptions || options || {});
        return;
      }
      const targetDescription = pendingMountTargetDescription(state.pendingMountOptions || options || {});
      clearPendingMountWait();
      throw new Error("HaeorumAISearch target not found before mountWaitMs: " + targetDescription);
    }, mountWaitMs());
  }

  function shouldMountFallbackFloating() {
    return state.options.fallbackFloating && state.options.autoAttach !== false && document.body;
  }

  function mountFallbackFloating(options) {
    const fallbackOptions = Object.assign({}, options || {}, {
      target: "",
      attachToSearchInput: "",
      attachAfterSelector: "",
      fallbackFloating: true
    });
    clearPendingMountWait();
    init(fallbackOptions);
  }

  function clearPendingMountWait() {
    if (state.pendingMountObserver && typeof state.pendingMountObserver.disconnect === "function") {
      state.pendingMountObserver.disconnect();
    }
    if (state.pendingMountTimer && typeof window.clearTimeout === "function") {
      window.clearTimeout(state.pendingMountTimer);
    }
    state.pendingMountOptions = null;
    state.pendingMountObserver = null;
    state.pendingMountTimer = null;
  }

  function pendingMountTargetsReady(options) {
    const explicitTargetSelector = trimText(options.target);
    const explicitSearchInputSelector = trimText(options.attachToSearchInput);
    const explicitAttachAfterSelector = trimText(options.attachAfterSelector);
    const explicitMissingSelector = (
      (explicitTargetSelector && !findElement(explicitTargetSelector)) ||
      (explicitSearchInputSelector && !findElement(explicitSearchInputSelector)) ||
      (explicitAttachAfterSelector && !findElement(explicitAttachAfterSelector))
    );
    const mountTargets = resolveMountTargets(explicitMissingSelector);
    return Boolean(mountTargets.target || mountTargets.attachAnchor);
  }

  function pendingMountTargetDescription(options) {
    const selectors = [
      trimText(options.target || state.options.target),
      trimText(options.attachToSearchInput || state.options.attachToSearchInput),
      trimText(options.attachAfterSelector || state.options.attachAfterSelector)
    ].filter(Boolean);
    return selectors.length ? selectors.join(", ") : "autoAttach search input";
  }

  function getMutationObserverConstructor() {
    if (window.MutationObserver) {
      return window.MutationObserver;
    }
    if (typeof MutationObserver !== "undefined") {
      return MutationObserver;
    }
    return null;
  }

  function resolveMountTargets(explicitMissingSelector) {
    const target = state.options.target ? findElement(state.options.target) : null;
    const searchInput = state.options.attachToSearchInput ? findElement(state.options.attachToSearchInput) : null;
    const attachAfter = state.options.attachAfterSelector ? findElement(state.options.attachAfterSelector) : null;
    const attachAnchor = state.options.attachAfterSelector ? attachAfter : searchInput;
    if (target || attachAnchor || state.options.autoAttach === false || explicitMissingSelector) {
      return { target: target, searchInput: searchInput, attachAnchor: attachAnchor, mode: target ? "target" : "configured" };
    }
    const autoInput = findAutoSearchInput();
    if (!autoInput) {
      if (state.options.fallbackFloating && document.body && !explicitMissingSelector) {
        return { target: null, searchInput: null, attachAnchor: document.body, mode: "floating" };
      }
      return { target: null, searchInput: null, attachAnchor: null, mode: "missing" };
    }
    const autoAnchor = findAutoAttachAnchor(autoInput) || autoInput;
    return { target: null, searchInput: autoInput, attachAnchor: autoAnchor, mode: "auto" };
  }

  function findElement(selector) {
    const selectorText = trimText(selector);
    if (!selectorText) {
      return null;
    }
    let found = querySingleElementOrNull(selectorText);
    if (found) {
      return found;
    }
    const escapedSelector = escapeHashIdSelectorTokens(selectorText);
    if (escapedSelector !== selectorText) {
      found = querySingleElementOrNull(escapedSelector);
      if (found) {
        return found;
      }
    }
    return findElementBySimpleIdSelector(selectorText);
  }

  function querySingleElementOrNull(selector) {
    try {
      if (document.querySelectorAll) {
        const matches = arrayFrom(document.querySelectorAll(selector));
        if (matches.length > 1) {
          throw ambiguousSelectorError(selector, matches.length);
        }
        return matches[0] || null;
      }
      return document.querySelector(selector);
    } catch (error) {
      if (isAmbiguousSelectorError(error)) {
        throw error;
      }
      return null;
    }
  }

  function ambiguousSelectorError(selector, count) {
    const error = new Error("HaeorumAISearch selector matched multiple elements: " + selector + " (" + count + ")");
    error.name = "HaeorumAISearchSelectorError";
    return error;
  }

  function isAmbiguousSelectorError(error) {
    return error && error.name === "HaeorumAISearchSelectorError";
  }

  function escapeHashIdSelectorTokens(selector) {
    return String(selector || "").replace(/#([^\s>+~,#[\]]+)/g, function (_match, id) {
      const escaped = escapeCssIdentifier(id);
      return "#" + escaped;
    });
  }

  function escapeCssIdentifier(value) {
    return String(value || "").replace(/[^A-Za-z0-9_-]/g, "\\$&");
  }

  function findElementBySimpleIdSelector(selector) {
    if (!document.getElementById) {
      return null;
    }
    const selectorText = trimText(selector);
    if (!selectorText || selectorText.charAt(0) !== "#" || /[\s>+~,]/.test(selectorText)) {
      return null;
    }
    const id = selectorText.slice(1);
    if (!id || !/[^\w-]/.test(id)) {
      return null;
    }
    return document.getElementById(id);
  }

  function findAutoSearchInput() {
    const inputs = arrayFrom(document.querySelectorAll ? document.querySelectorAll("input") : []);
    let bestInput = null;
    let bestScore = 0;
    inputs.forEach(function (input) {
      const score = scoreSearchInput(input);
      if (score > bestScore) {
        bestInput = input;
        bestScore = score;
      }
    });
    return bestScore > 0 ? bestInput : null;
  }

  function scoreSearchInput(input) {
    const type = lowerAttr(input, "type") || lowerText(input.type) || "text";
    if (["hidden", "submit", "button", "checkbox", "radio", "file", "image", "password", "reset"].indexOf(type) !== -1) {
      return 0;
    }
    if (input.disabled || input.readOnly || lowerAttr(input, "aria-disabled") === "true" || isElementHidden(input)) {
      return 0;
    }
    const id = lowerAttr(input, "id");
    const name = lowerAttr(input, "name");
    const classes = lowerText(input.className);
    const placeholder = lowerAttr(input, "placeholder");
    const label = lowerAttr(input, "aria-label") + " " + lowerAttr(input, "title");
    const haystack = [id, name, classes, placeholder, label].join(" ");
    let score = 0;
    if (type === "search") {
      score += 60;
    }
    if (/(^|[-_\s])(search|srch|keyword|query|q|s)([-_\s]|$)/.test([id, name, classes].join(" "))) {
      score += 40;
    }
    if (/(검색|상품명|품명|키워드|찾기)/.test(haystack)) {
      score += 40;
    }
    if (/(search|srch|keyword|query|product)/.test(haystack)) {
      score += 30;
    }
    return score;
  }

  function findAutoAttachAnchor(input) {
    const form = closestTag(input, "form");
    if (!form || !form.querySelectorAll) {
      return null;
    }
    const controls = arrayFrom(form.querySelectorAll("button")).concat(arrayFrom(form.querySelectorAll("input")));
    let fallback = null;
    for (let index = 0; index < controls.length; index += 1) {
      const control = controls[index];
      if (!isSubmitLikeControl(control)) {
        continue;
      }
      if (!fallback) {
        fallback = control;
      }
      if (scoreSearchAction(control) > 0) {
        return control;
      }
    }
    return fallback;
  }

  function isSubmitLikeControl(control) {
    const tagName = lowerText(control.tagName);
    const type = lowerAttr(control, "type") || lowerText(control.type);
    if (tagName === "button") {
      return !type || ["submit", "button"].indexOf(type) !== -1;
    }
    if (tagName === "input") {
      return ["submit", "button", "image"].indexOf(type) !== -1;
    }
    return false;
  }

  function scoreSearchAction(control) {
    const haystack = [
      lowerAttr(control, "id"),
      lowerAttr(control, "name"),
      lowerText(control.className),
      lowerAttr(control, "value"),
      lowerAttr(control, "aria-label"),
      lowerAttr(control, "title"),
      lowerText(control.textContent),
    ].join(" ");
    return /(검색|찾기|search|srch|keyword|query)/.test(haystack) ? 1 : 0;
  }

  function closestTag(element, tagName) {
    let current = element;
    const expected = String(tagName || "").toUpperCase();
    while (current) {
      if (current.tagName === expected) {
        return current;
      }
      current = current.parentNode;
    }
    return null;
  }

  function lowerAttr(element, name) {
    if (!element || typeof element.getAttribute !== "function") {
      return "";
    }
    return lowerText(element.getAttribute(name));
  }

  function lowerText(value) {
    return String(value || "").toLowerCase();
  }

  function arrayFrom(value) {
    return Array.prototype.slice.call(value || []);
  }

  function mountWaitMs() {
    const value = Number(state.options.mountWaitMs);
    const finite = Number.isFinite ? Number.isFinite(value) : isFinite(value);
    return Math.max(0, Math.floor(finite ? value : DEFAULTS.mountWaitMs));
  }

  function resetExistingWidget() {
    if (!state.root && !state.modal && !state.objectUrl) {
      return;
    }
    if (state.objectUrl && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(state.objectUrl);
    }
    state.file = null;
    state.objectUrl = null;
    state.imageValidationPending = false;
    state.lastFocusedElement = null;
    state.lastQuery = "";
    state.lastCategory = "";
    state.lastQueryType = "";
    state.nextOffset = null;
    removeNode(state.modal);
    if (state.root) {
      if (state.root.classList && state.root.classList.contains("hai-attached")) {
        removeNode(state.root);
      } else {
        state.root.innerHTML = "";
        if (state.root.classList) {
          state.root.classList.remove("hai-root");
        }
        if (typeof state.root.removeAttribute === "function") {
          state.root.removeAttribute("data-hai-attach-mode");
        }
      }
    }
    state.root = null;
    state.modal = null;
    state.searchInput = null;
  }

  function removeNode(node) {
    if (!node) {
      return;
    }
    if (typeof node.remove === "function") {
      node.remove();
      return;
    }
    if (node.parentNode && typeof node.parentNode.removeChild === "function") {
      node.parentNode.removeChild(node);
    }
  }

  function mountRoot(target, attachAnchor, mode) {
    if (target) {
      target.classList.add("hai-root");
      target.setAttribute("data-hai-attach-mode", mode || "target");
      target.innerHTML = "";
      return target;
    }
    const root = document.createElement("span");
    root.className = "hai-root hai-attached";
    root.setAttribute("data-hai-attach-mode", mode || "configured");
    if (mode === "floating") {
      document.body.appendChild(root);
    } else {
      attachAnchor.insertAdjacentElement("afterend", root);
    }
    return root;
  }

  function buildTrigger(root) {
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "hai-trigger";
    trigger.setAttribute("aria-label", state.options.triggerAriaLabel || DEFAULTS.triggerAriaLabel);
    trigger.title = state.options.triggerTitle || DEFAULTS.triggerTitle;
    trigger.innerHTML = aiSearchIcon() + '<span class="hai-trigger-label">AI 이미지검색</span>';
    trigger.addEventListener("click", open);
    root.appendChild(trigger);
  }

  function buildModal() {
    const modal = document.createElement("div");
    modal.className = "hai-modal";
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML = `
      <div class="hai-backdrop" data-close="true"></div>
      <section class="hai-dialog" role="dialog" aria-modal="true" aria-labelledby="hai-title">
        <header class="hai-header">
          <div class="hai-brand-head">
            <div class="hai-brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
            <div>
              <div class="hai-eyebrow">해오름 판촉물 AI 검색</div>
              <h2 id="hai-title">상품명과 사진으로 비슷한 판촉물을 찾습니다</h2>
              <p>기존 검색 결과 흐름은 유지하면서, AI가 이미지와 의미를 함께 비교해 추천합니다.</p>
              <div class="hai-badges" aria-hidden="true">
                <span>이미지 검색</span>
                <span>유사도 추천</span>
                <span>상품 상세 연결</span>
              </div>
            </div>
          </div>
          <button type="button" class="hai-icon-button" data-close="true" aria-label="닫기">${xIcon()}</button>
        </header>
        <div class="hai-body">
          <form class="hai-search-form">
            <div class="hai-mode-strip" aria-hidden="true">
              <span class="hai-mode-active">AI 상품 이미지 검색</span>
              <span>상품명 검색</span>
              <span>비슷한 카테고리</span>
            </div>
            <div class="hai-row">
              <label class="hai-field">
                <span>찾고 싶은 판촉물</span>
                <input class="hai-query" name="q" type="search" placeholder="파란색 앞치마, 투명 크리스탈 상패">
              </label>
              <button type="submit" class="hai-primary">AI 검색</button>
            </div>
            <div class="hai-dropzone" tabindex="0" role="button" aria-label="상품 이미지 업로드">
              <input class="hai-file" type="file" accept="image/jpeg,image/png,image/webp" aria-label="상품 이미지 파일 선택">
              <div class="hai-upload-icon" aria-hidden="true">${aiSearchIcon()}</div>
              <div class="hai-upload-copy">
                <strong>사진으로 비슷한 상품 찾기</strong>
                <span>클릭 또는 드래그 앤 드롭 (JPG, PNG, WEBP, 최대 ${escapeHtml(String(state.options.maxImageMb))}MB, 최소 ${escapeHtml(String(minImageDimension()))}px)</span>
              </div>
            </div>
            <div class="hai-preview" hidden>
              <img alt="업로드 이미지 미리보기">
              <button type="button" class="hai-secondary hai-remove">이미지 삭제</button>
            </div>
          </form>
          <div class="hai-error" role="alert" hidden></div>
          <div class="hai-loading" role="status" aria-live="polite" hidden>검색 중입니다.</div>
          <div class="hai-notice" role="status" hidden></div>
          <section class="hai-categories" hidden>
            <h3><span></span>비슷한 카테고리 추천</h3>
            <div class="hai-category-list"></div>
          </section>
          <section class="hai-top" hidden>
            <h3><span></span>상위 유사 상품 3개</h3>
            <div class="hai-top-list"></div>
          </section>
          <section class="hai-items" hidden>
            <h3><span></span>관련 상품 리스트</h3>
            <div class="hai-item-list"></div>
            <div class="hai-more-wrap" hidden>
              <button type="button" class="hai-secondary hai-more">더보기</button>
            </div>
          </section>
          <div class="hai-empty" hidden>검색 결과가 부족합니다. 다른 검색어나 이미지를 사용해 주세요.</div>
        </div>
      </section>
    `;
    document.body.appendChild(modal);
    state.modal = modal;
    applyDesignOptions(modal);
    bindModal(modal);
  }

  function applyDesignOptions(element) {
    setCssVar(element, "--hai-accent", state.options.accentColor || DEFAULTS.accentColor);
    setCssVar(element, "--hai-accent-text", state.options.accentTextColor || DEFAULTS.accentTextColor);
    setCssVar(element, "--hai-accent-soft", state.options.accentSoftColor || DEFAULTS.accentSoftColor);
    setCssVar(element, "--hai-z-index", String(state.options.zIndex || DEFAULTS.zIndex));
  }

  function setCssVar(element, name, value) {
    if (element.style && typeof element.style.setProperty === "function") {
      element.style.setProperty(name, String(value));
      return;
    }
    if (element.style) {
      element.style[name] = String(value);
    }
  }

  function bindModal(modal) {
    modal.addEventListener("click", function (event) {
      if (event.target && event.target.dataset.close === "true") {
        close();
      }
    });
    modal.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "Tab") {
        trapModalFocus(event);
      }
    });
    modal.querySelector(".hai-search-form").addEventListener("submit", function (event) {
      event.preventDefault();
      submitSearch();
    });
    const dropzone = modal.querySelector(".hai-dropzone");
    const fileInput = modal.querySelector(".hai-file");
    dropzone.addEventListener("click", function () { fileInput.click(); });
    dropzone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        fileInput.click();
      }
    });
    fileInput.addEventListener("change", function () {
      if (fileInput.files && fileInput.files[0]) {
        setFile(fileInput.files[0]);
      }
    });
    ["dragenter", "dragover"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.add("hai-drag");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.remove("hai-drag");
      });
    });
    dropzone.addEventListener("drop", function (event) {
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        setFile(file);
      }
    });
    modal.querySelector(".hai-remove").addEventListener("click", clearFile);
    modal.querySelector(".hai-more").addEventListener("click", function () {
      submitSearch(state.lastCategory, true);
    });
  }

  function open() {
    state.lastFocusedElement = document.activeElement || null;
    state.modal.classList.add("hai-open");
    state.modal.setAttribute("aria-hidden", "false");
    const input = state.modal.querySelector(".hai-query");
    if (state.options.prefillFromSearchInput && state.searchInput && state.searchInput.value) {
      input.value = state.searchInput.value.trim();
    }
    window.setTimeout(function () { input.focus(); }, 30);
  }

  function close() {
    state.modal.classList.remove("hai-open");
    state.modal.setAttribute("aria-hidden", "true");
    if (state.lastFocusedElement && typeof state.lastFocusedElement.focus === "function") {
      state.lastFocusedElement.focus();
    }
  }

  function setFile(file) {
    clearError();
    clearFile();
    if (!isAllowedImageFile(file)) {
      showError("JPG, PNG, WEBP 이미지만 업로드할 수 있습니다.");
      return;
    }
    const maxBytes = state.options.maxImageMb * 1024 * 1024;
    if (file.size > maxBytes) {
      showError("이미지 용량이 제한을 초과했습니다.");
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    state.objectUrl = objectUrl;
    state.imageValidationPending = true;
    const preview = state.modal.querySelector(".hai-preview");
    const previewImage = preview.querySelector("img");
    preview.hidden = true;
    previewImage.onload = function () {
      if (state.objectUrl !== objectUrl) {
        return;
      }
      state.imageValidationPending = false;
      if (!isLoadedImageLargeEnough(previewImage)) {
        clearFile();
        showError("이미지 가로/세로는 최소 " + minImageDimension() + "px 이상이어야 합니다.");
        return;
      }
      state.file = file;
      preview.hidden = false;
    };
    previewImage.onerror = function () {
      if (state.objectUrl !== objectUrl) {
        return;
      }
      clearFile();
      showError("이미지를 읽을 수 없습니다. 다른 JPG, PNG, WEBP 파일을 사용해 주세요.");
    };
    previewImage.src = objectUrl;
  }

  function isAllowedImageFile(file) {
    const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
    if (allowedTypes.indexOf(file.type) !== -1) {
      return true;
    }
    return /\.(jpe?g|png|webp)$/i.test(file.name || "");
  }

  function clearFile() {
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
    }
    state.file = null;
    state.objectUrl = null;
    state.imageValidationPending = false;
    state.modal.querySelector(".hai-file").value = "";
    const preview = state.modal.querySelector(".hai-preview");
    const previewImage = preview.querySelector("img");
    previewImage.onload = null;
    previewImage.onerror = null;
    previewImage.src = "";
    preview.hidden = true;
  }

  function minImageDimension() {
    const value = Number(state.options.minImageDimension || DEFAULTS.minImageDimension);
    const finite = Number.isFinite ? Number.isFinite(value) : isFinite(value);
    return Math.max(1, Math.floor(finite ? value : DEFAULTS.minImageDimension));
  }

  function isLoadedImageLargeEnough(image) {
    const minimum = minImageDimension();
    const width = Number(image.naturalWidth || image.width || 0);
    const height = Number(image.naturalHeight || image.height || 0);
    return width >= minimum && height >= minimum;
  }

  async function submitSearch(category, append) {
    if (state.searchInFlight) {
      return;
    }
    clearError();
    const query = state.modal.querySelector(".hai-query").value.trim();
    state.lastQuery = query;
    if (!append) {
      state.lastCategory = category || "";
      state.nextOffset = null;
    }
    if (state.imageValidationPending) {
      showError("이미지를 확인 중입니다. 잠시 후 다시 검색해 주세요.");
      return;
    }
    if (!query && !state.file) {
      showError("검색어를 입력하거나 이미지를 업로드해 주세요.");
      return;
    }
    if (!append && state.options.resultPageUrl) {
      state.searchInFlight = true;
      setLoading(true);
      try {
        await redirectToResultPage(query, state.lastCategory);
      } catch (error) {
        showError(normalizeSearchError(error));
      } finally {
        state.searchInFlight = false;
        setLoading(false);
      }
      return;
    }
    if (!append) {
      clearResults();
    }
    state.searchInFlight = true;
    setLoading(true);
    try {
      const form = new FormData();
      form.append("mall_id", state.options.mallId);
      form.append("limit", String(state.options.limit));
      form.append("offset", String(append ? (state.nextOffset || 0) : 0));
      if (query) {
        form.append("q", query);
      }
      if (state.lastCategory) {
        form.append("category", state.lastCategory);
      }
      if (state.file) {
        form.append("image", state.file);
      }
      const response = await fetch(trimSlash(state.options.apiBaseUrl) + "/api/ai-search", {
        method: "POST",
        headers: apiHeaders(),
        body: form
      });
      const data = await readJsonSafely(response);
      if (!response.ok) {
        throw new Error(apiErrorMessage(response.status, data));
      }
      renderResults(data, Boolean(append));
    } catch (error) {
      showError(normalizeSearchError(error));
    } finally {
      state.searchInFlight = false;
      setLoading(false);
    }
  }

  async function readJsonSafely(response) {
    try {
      return await response.json();
    } catch (error) {
      return {};
    }
  }

  function apiErrorMessage(status, data) {
    const raw = String((data && (data.detail || data.message)) || "");
    if (/gemini backend unavailable|resource_exhausted|prepayment credits|quota/i.test(raw)) {
      return "사진/검색어 처리 크레딧이 소진되어 새 검색을 만들 수 없습니다. Gemini 결제/크레딧을 보충하면 바로 정상 동작합니다.";
    }
    if (status === 401 || status === 403) {
      return "현재 사이트에서 AI 검색을 사용할 수 없습니다.";
    }
    if (status === 413) {
      return "이미지 용량이 제한을 초과했습니다.";
    }
    if (status === 429) {
      return "요청이 많습니다. 잠시 후 다시 검색해 주세요.";
    }
    if (status >= 500) {
      return "AI 검색 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.";
    }
    return raw || "검색 요청에 실패했습니다.";
  }

  function normalizeSearchError(error) {
    const message = error && error.message ? error.message : String(error || "");
    if (/failed to fetch|networkerror|load failed/i.test(message)) {
      return "AI 검색 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
    }
    return message || "검색 요청에 실패했습니다.";
  }

  async function redirectToResultPage(query, category) {
    const sessionId = newSearchSessionId();
    const imagePayload = state.file ? await fileToDataUrl(state.file) : null;
    const payload = {
      version: 1,
      created_at: new Date().toISOString(),
      apiBaseUrl: state.options.apiBaseUrl,
      mallId: state.options.mallId,
      apiKey: state.options.apiKey || "",
      limit: state.options.limit,
      query: query || "",
      category: category || "",
      image: imagePayload ? {
        dataUrl: imagePayload,
        name: state.file.name || "upload-image",
        type: state.file.type || "application/octet-stream",
        size: state.file.size || 0
      } : null
    };
    const stored = storeSearchSession(sessionId, payload);
    if (!stored && state.file) {
      throw new Error("이미지 검색 정보를 결과 페이지로 넘길 수 없습니다. 이미지를 조금 더 작게 줄여 다시 시도해 주세요.");
    }
    const url = resultPageUrl(sessionId, query, category);
    if (state.options.resultPageTarget === "_blank") {
      const opened = window.open(url, "_blank", "noopener,noreferrer");
      if (!opened) {
        window.location.href = url;
      }
      return;
    }
    window.location.href = url;
  }

  function storeSearchSession(sessionId, payload) {
    let stored = false;
    const key = "haeorumAiSearch:" + sessionId;
    try {
      if (window.sessionStorage) {
        window.sessionStorage.setItem(key, JSON.stringify(payload));
        stored = true;
      }
    } catch (error) {
    }
    try {
      if (window.localStorage) {
        window.localStorage.setItem(key, JSON.stringify(payload));
        stored = true;
      }
    } catch (error) {
    }
    return stored;
  }

  function resultPageUrl(sessionId, query, category) {
    const url = new URL(state.options.resultPageUrl, window.location.href);
    if (sessionId) {
      url.searchParams.set("hai_sid", sessionId);
    }
    if (query) {
      url.searchParams.set("q", query);
    }
    if (category) {
      url.searchParams.set("category", category);
    }
    return url.toString();
  }

  function newSearchSessionId() {
    const random = Math.random().toString(36).slice(2, 10);
    return String(Date.now()) + "-" + random;
  }

  function fileToDataUrl(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("이미지를 읽을 수 없습니다.")); };
      reader.readAsDataURL(file);
    });
  }

  function clearResults() {
    [".hai-category-list", ".hai-top-list", ".hai-item-list"].forEach(function (selector) {
      state.modal.querySelector(selector).innerHTML = "";
    });
    [".hai-categories", ".hai-top", ".hai-items", ".hai-notice", ".hai-empty"].forEach(function (selector) {
      const element = state.modal.querySelector(selector);
      element.hidden = true;
    });
    state.modal.querySelector(".hai-more-wrap").hidden = true;
  }

  function renderResults(data, append) {
    const top = Array.isArray(data.top) ? data.top : [];
    const items = Array.isArray(data.items) ? data.items : [];
    const meta = data.meta || {};
    if (!append) {
      renderNotice(meta);
      renderCategories(data.suggested_categories);
      renderProductList(".hai-top", ".hai-top-list", top, false, 1);
      renderProductList(".hai-items", ".hai-item-list", items, false, 4 + (meta.offset || 0));
      const hasApiNotice = Boolean(meta.low_confidence && meta.notice);
      state.modal.querySelector(".hai-empty").hidden = top.length + items.length > 0 || hasApiNotice;
      state.lastQueryType = meta.query_type || deriveQueryType(state.lastQuery, state.file);
    } else {
      renderProductList(".hai-items", ".hai-item-list", items, true, 4 + (meta.offset || 0));
    }
    state.nextOffset = meta.has_more ? meta.next_offset : null;
    renderMore(meta);
  }

  function renderNotice(meta) {
    const notice = state.modal.querySelector(".hai-notice");
    if (meta.low_confidence && meta.notice) {
      notice.textContent = meta.notice;
      notice.hidden = false;
      return;
    }
    notice.textContent = "";
    notice.hidden = true;
  }

  function renderCategories(categories) {
    const section = state.modal.querySelector(".hai-categories");
    const list = section.querySelector(".hai-category-list");
    const values = Array.isArray(categories) ? categories.filter(Boolean).slice(0, 15) : [];
    list.innerHTML = "";
    section.hidden = values.length === 0;
    values.forEach(function (category) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "hai-chip";
      button.textContent = category;
      button.addEventListener("click", function () { submitSearch(category); });
      list.appendChild(button);
    });
  }

  function renderProductList(sectionSelector, listSelector, products, append, basePosition) {
    const section = state.modal.querySelector(sectionSelector);
    const list = state.modal.querySelector(listSelector);
    if (!append) {
      list.innerHTML = "";
      section.hidden = products.length === 0;
    }
    if (append && products.length) {
      section.hidden = false;
    }
    products.forEach(function (product, index) {
      const card = document.createElement("article");
      card.className = "hai-card";
      const price = product.price === null || product.price === undefined ? "견적문의상품" : numberWithCommas(product.price) + "원~";
      const scoreText = formatScore(product);
      const productName = product.name || "";
      const productId = product.product_id || "";
      const productUrl = safeLinkUrl(product.product_url);
      const clickProduct = Object.assign({}, product, { product_url: productUrl === "#" ? null : productUrl });
      const imageUrl = safeImageUrl(product.image_url);
      card.innerHTML = `
        <a class="hai-image-link" href="${escapeAttr(productUrl)}" target="_blank" rel="noreferrer" aria-label="${escapeAttr(productName || productId || "상품 상세 보기")}">
          <span class="hai-score">${escapeHtml(scoreText)}</span>
          ${imageUrl ? `<img src="${escapeAttr(imageUrl)}" alt="${escapeAttr(productName)}">` : '<span class="hai-image-empty">이미지 없음</span>'}
        </a>
        <div class="hai-card-body">
          <div class="hai-product-id">상품번호 ${escapeHtml(productId)}</div>
          <h4>${escapeHtml(productName)}</h4>
          <div class="hai-card-meta">${escapeHtml(product.category || "")}</div>
          <div class="hai-price">${escapeHtml(price)}</div>
          <a class="hai-detail" href="${escapeAttr(productUrl)}" target="_blank" rel="noreferrer">상세 보기</a>
        </div>
      `;
      Array.prototype.forEach.call(card.querySelectorAll("a"), function (link) {
        link.addEventListener("click", function () {
          recordClick(clickProduct, (basePosition || 1) + index);
        });
      });
      const image = card.querySelector("img");
      if (image) {
        image.addEventListener("error", function () {
          replaceImageWithEmptyState(image);
        });
      }
      list.appendChild(card);
    });
  }

  function replaceImageWithEmptyState(image) {
    const empty = document.createElement("span");
    empty.className = "hai-image-empty";
    empty.textContent = "이미지 없음";
    if (typeof image.replaceWith === "function") {
      image.replaceWith(empty);
      return;
    }
    if (image.parentNode) {
      image.parentNode.replaceChild(empty, image);
    }
  }

  function renderMore(meta) {
    const wrap = state.modal.querySelector(".hai-more-wrap");
    wrap.hidden = !(meta && meta.has_more && meta.next_offset !== null && meta.next_offset !== undefined);
  }

  function recordClick(product, position) {
    const payload = JSON.stringify({
      mall_id: state.options.mallId,
      product_id: product.product_id,
      product_url: product.product_url,
      position: position,
      query: state.lastQuery,
      score_percent: product.score_percent,
      query_type: state.lastQueryType || deriveQueryType(state.lastQuery, state.file)
    });
    const url = trimSlash(state.options.apiBaseUrl) + "/api/click-log";
    if (!state.options.apiKey && typeof navigator !== "undefined" && navigator.sendBeacon) {
      const blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon(url, blob);
      return;
    }
    fetch(url, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, apiHeaders()),
      body: payload,
      keepalive: true
    }).catch(function () {});
  }

  function apiHeaders() {
    if (!state.options.apiKey) {
      return {};
    }
    return { "X-API-Key": state.options.apiKey };
  }

  function deriveQueryType(query, file) {
    if (query && file) {
      return "text_image";
    }
    if (file) {
      return "image";
    }
    return "text";
  }

  function setLoading(loading) {
    state.modal.querySelector(".hai-loading").hidden = !loading;
    state.modal.querySelector(".hai-primary").disabled = loading;
    state.modal.querySelector(".hai-more").disabled = loading;
  }

  function showError(message) {
    const error = state.modal.querySelector(".hai-error");
    error.textContent = message;
    error.hidden = false;
  }

  function clearError() {
    const error = state.modal.querySelector(".hai-error");
    error.textContent = "";
    error.hidden = true;
  }

  function trapModalFocus(event) {
    const focusable = getFocusableElements();
    if (!focusable.length) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !isElementInside(state.modal, active))) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function getFocusableElements() {
    const nodes = state.modal.querySelectorAll("a[href], button, input, [tabindex]");
    return Array.prototype.filter.call(nodes, function (element) {
      const tabindex = element.getAttribute ? element.getAttribute("tabindex") : null;
      return !element.disabled && tabindex !== "-1" && !isElementHidden(element) && !hasClass(element, "hai-file");
    });
  }

  function isElementHidden(element) {
    let current = element;
    while (current && current !== state.modal) {
      if (current.hidden || current.getAttribute && current.getAttribute("aria-hidden") === "true") {
        return true;
      }
      if (current.getAttribute && inlineStyleHidesElement(current.getAttribute("style"))) {
        return true;
      }
      if (hasClass(current, "hidden") || hasClass(current, "hide") || hasClass(current, "d-none") || hasClass(current, "display-none") || hasClass(current, "sr-only") || hasClass(current, "visually-hidden") || hasClass(current, "screen-reader-text")) {
        return true;
      }
      if (typeof window.getComputedStyle === "function") {
        const computed = window.getComputedStyle(current);
        if (computed && (computed.display === "none" || computed.visibility === "hidden" || computed.visibility === "collapse")) {
          return true;
        }
      }
      current = current.parentNode;
    }
    return false;
  }

  function inlineStyleHidesElement(styleText) {
    const normalized = String(styleText || "").toLowerCase().replace(/\s+/g, "");
    return normalized.indexOf("display:none") !== -1 || normalized.indexOf("visibility:hidden") !== -1 || normalized.indexOf("visibility:collapse") !== -1;
  }

  function isElementInside(parent, child) {
    let current = child;
    while (current) {
      if (current === parent) {
        return true;
      }
      current = current.parentNode;
    }
    return false;
  }

  function hasClass(element, className) {
    return element.classList && element.classList.contains(className);
  }

  function injectStyles() {
    if (document.getElementById("haeorum-ai-search-style")) {
      return;
    }
    const style = document.createElement("style");
    style.id = "haeorum-ai-search-style";
    style.textContent = `
      .hai-root { display: inline-flex; vertical-align: middle; }
      .hai-root.hai-attached { margin-left: 8px; }
      .hai-root[data-hai-attach-mode="floating"] {
        position: fixed; right: 20px; bottom: 20px; z-index: var(--hai-z-index, 2147483000); margin-left: 0;
      }
      .hai-trigger, .hai-icon-button {
        width: 40px; height: 40px; display: inline-flex; align-items: center; justify-content: center;
        border: 1px solid #cfd6df; border-radius: 6px; background: #fff; color: #172033; cursor: pointer;
      }
      .hai-trigger {
        width: auto; min-width: 82px; padding: 0 10px 0 8px; gap: 4px;
        border-color: var(--hai-accent, #0f766e); color: var(--hai-accent, #0f766e);
        background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, .12);
        font-size: 13px; font-weight: 800; white-space: nowrap;
      }
      .hai-trigger:hover {
        background: var(--hai-accent, #0f766e); color: var(--hai-accent-text, #fff);
      }
      .hai-icon-button:hover { background: #f2f5f8; border-color: var(--hai-accent, #0f766e); }
      .hai-trigger svg { width: 25px; height: 25px; flex: 0 0 auto; }
      .hai-trigger-label { line-height: 1; }
      .hai-icon-button svg { width: 20px; height: 20px; }
      .hai-modal { position: fixed; inset: 0; z-index: var(--hai-z-index, 2147483000); display: none; font-family: Arial, "Noto Sans KR", sans-serif; letter-spacing: 0; color: #172033; }
      .hai-modal * { box-sizing: border-box; letter-spacing: 0; }
      .hai-modal [hidden] { display: none !important; }
      .hai-open { display: block; }
      .hai-backdrop { position: absolute; inset: 0; background: rgba(15, 23, 42, 0.46); }
      .hai-dialog {
        position: relative; width: min(1040px, calc(100vw - 28px)); max-height: calc(100vh - 36px); margin: 18px auto;
        background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 24px 70px rgba(15, 23, 42, 0.24);
        display: grid; grid-template-rows: auto minmax(0, 1fr);
      }
      .hai-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid #d9e0e8; }
      .hai-header h2 { margin: 0 0 5px; font-size: 20px; line-height: 1.2; }
      .hai-header p { margin: 0; color: #5b6778; font-size: 13px; line-height: 1.45; }
      .hai-body { padding: 18px 20px 22px; overflow: auto; display: grid; gap: 16px; }
      .hai-search-form { display: grid; gap: 12px; }
      .hai-row { display: grid; grid-template-columns: minmax(0, 1fr) 92px; gap: 10px; align-items: end; }
      .hai-field { display: grid; gap: 6px; font-size: 12px; color: #5b6778; font-weight: 700; }
      .hai-field input {
        width: 100%; min-height: 42px; border: 1px solid #cfd6df; border-radius: 6px; padding: 9px 11px; font: inherit; color: #172033;
      }
      .hai-primary, .hai-secondary, .hai-chip, .hai-detail {
        min-height: 42px; border: 0; border-radius: 6px; padding: 0 14px; font-weight: 700; font-size: 14px; cursor: pointer; text-decoration: none;
      }
      .hai-primary { background: var(--hai-accent, #0f766e); color: var(--hai-accent-text, #fff); }
      .hai-secondary, .hai-chip { background: #fff; color: #172033; border: 1px solid #cfd6df; }
      .hai-primary:disabled { opacity: .58; cursor: wait; }
      .hai-dropzone {
        position: relative; min-height: 118px; border: 1px dashed #96a3b5; border-radius: 8px; background: #f8fafc;
        display: grid; place-items: center; text-align: center; cursor: pointer; padding: 18px;
      }
      .hai-dropzone.hai-drag { border-color: var(--hai-accent, #0f766e); background: var(--hai-accent-soft, #ecfdf5); }
      .hai-file { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
      .hai-upload-copy { display: grid; gap: 5px; color: #5b6778; font-size: 13px; }
      .hai-upload-copy strong { color: #172033; font-size: 15px; }
      .hai-preview { display: flex; align-items: center; gap: 12px; }
      .hai-preview img { width: 92px; height: 92px; object-fit: cover; border-radius: 8px; border: 1px solid #d9e0e8; }
      .hai-error { border: 1px solid #f2b8b5; background: #fff1f2; color: #b42318; border-radius: 6px; padding: 10px 12px; font-size: 13px; }
      .hai-loading { border: 1px solid #bfdbfe; background: #eff6ff; color: #1e40af; border-radius: 6px; padding: 10px 12px; font-size: 13px; }
      .hai-notice { border: 1px solid #fde68a; background: #fffbeb; color: #92400e; border-radius: 6px; padding: 10px 12px; font-size: 13px; }
      .hai-categories, .hai-top, .hai-items { display: grid; gap: 10px; }
      .hai-categories h3, .hai-top h3, .hai-items h3 { margin: 0; font-size: 15px; line-height: 1.25; }
      .hai-category-list { display: flex; flex-wrap: wrap; gap: 8px; }
      .hai-chip { min-height: 34px; font-size: 13px; }
      .hai-top-list, .hai-item-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
      .hai-top-list { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .hai-card { border: 1px solid #d9e0e8; border-radius: 8px; overflow: hidden; background: #fff; min-width: 0; display: grid; }
      .hai-image-link { display: block; background: #edf1f5; }
      .hai-card img { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; }
      .hai-image-empty {
        width: 100%; aspect-ratio: 1 / 1; display: grid; place-items: center;
        color: #8a94a6; font-size: 13px; font-weight: 800; background: #f3f6f9;
      }
      .hai-card-body { padding: 11px; display: grid; gap: 7px; }
      .hai-score { color: var(--hai-accent, #0f766e); font-weight: 800; font-size: 12px; }
      .hai-product-id, .hai-card-meta { color: #667085; font-size: 12px; line-height: 1.35; }
      .hai-card h4 { margin: 0; font-size: 14px; line-height: 1.3; overflow-wrap: anywhere; }
      .hai-detail { display: inline-flex; align-items: center; justify-content: center; border: 1px solid #cfd6df; color: #172033; background: #fff; min-height: 36px; }
      .hai-empty { padding: 20px; text-align: center; border: 1px solid #d9e0e8; border-radius: 8px; color: #667085; }
      .hai-root { font-family: Arial, "Noto Sans KR", sans-serif; letter-spacing: 0; }
      .hai-trigger {
        min-width: 110px; height: 34px; border-radius: 0; padding: 0 11px; gap: 5px;
        border: 1px solid var(--hai-accent, #ee1b24); background: var(--hai-accent, #ee1b24);
        color: #fff; box-shadow: none; font-size: 12px; line-height: 1;
      }
      .hai-trigger:hover { background: #cf1119; border-color: #cf1119; color: #fff; }
      .hai-trigger svg { width: 18px; height: 18px; }
      .hai-dialog {
        width: min(1120px, calc(100vw - 32px)); border-radius: 0; border: 1px solid #d8d8d8;
        box-shadow: 0 20px 60px rgba(0, 0, 0, .28); overflow-x: hidden; min-width: 0;
      }
      .hai-backdrop { background: rgba(0, 0, 0, .42); }
      .hai-header {
        position: relative; padding: 0; border: 0; background: #fff;
      }
      .hai-header::before {
        content: ""; position: absolute; left: 0; right: 0; top: 0; height: 5px;
        background: linear-gradient(90deg, #ee1b24 0 38%, #ffca08 38% 52%, #0b4ea2 52% 100%);
      }
      .hai-brand-head {
        width: 100%; display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 14px;
        padding: 22px 58px 18px 22px; align-items: center; min-width: 0;
      }
      .hai-brand-mark { position: relative; width: 62px; height: 48px; }
      .hai-brand-mark span { position: absolute; border-radius: 12px; display: block; }
      .hai-brand-mark span:nth-child(1) { width: 36px; height: 36px; left: 0; top: 8px; background: #ffca08; }
      .hai-brand-mark span:nth-child(2) { width: 36px; height: 36px; left: 22px; top: 2px; background: rgba(238, 27, 36, .9); }
      .hai-brand-mark span:nth-child(3) { width: 32px; height: 32px; left: 30px; top: 18px; background: rgba(11, 78, 162, .85); }
      .hai-eyebrow { color: var(--hai-accent, #ee1b24); font-size: 13px; font-weight: 800; margin-bottom: 5px; }
      .hai-header h2 { margin: 0 0 6px; color: #111; font-size: 23px; font-weight: 900; line-height: 1.22; }
      .hai-header p { color: #555; font-size: 13px; }
      .hai-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
      .hai-badges span {
        display: inline-flex; align-items: center; min-height: 26px; padding: 0 9px;
        border: 1px solid #e4e4e4; background: #fafafa; color: #333; font-size: 12px; font-weight: 700;
      }
      .hai-badges span:first-child { border-color: #ffc6c9; background: #fff3f0; color: #d3111a; }
      .hai-icon-button { position: absolute; top: 14px; right: 14px; width: 34px; height: 34px; border-radius: 0; border-color: #ddd; }
      .hai-body { padding: 0 22px 24px; gap: 18px; background: #fff; }
      .hai-search-form {
        border: 1px solid #dedede; background: #fff; gap: 0;
        min-width: 0;
      }
      .hai-mode-strip {
        display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: stretch; min-height: 38px;
        background: #ee1b24; color: #fff; font-size: 13px; font-weight: 800; min-width: 0; max-width: 100%;
      }
      .hai-mode-strip span {
        display: flex; align-items: center; justify-content: center; min-width: 0; padding: 6px 10px;
        border-right: 1px solid rgba(255, 255, 255, .34); text-align: center; line-height: 1.25;
        white-space: normal; overflow-wrap: anywhere; word-break: keep-all;
      }
      .hai-mode-strip .hai-mode-active { background: #d51018; color: #fff; }
      .hai-row {
        grid-template-columns: minmax(0, 1fr) 116px; gap: 8px; align-items: stretch;
        padding: 13px; border-bottom: 1px solid #ededed; background: #fafafa;
        min-width: 0;
      }
      .hai-field { gap: 5px; color: #333; font-size: 12px; }
      .hai-field { min-width: 0; }
      .hai-field input {
        min-height: 44px; border: 2px solid var(--hai-accent, #ee1b24); border-radius: 0;
        padding: 0 14px; color: #111; font-size: 15px; background: #fff;
      }
      .hai-field input:focus { outline: 2px solid rgba(238, 27, 36, .18); outline-offset: 1px; }
      .hai-primary {
        min-height: 44px; border-radius: 0; background: var(--hai-accent, #ee1b24);
        color: #fff; font-size: 15px; font-weight: 900;
      }
      .hai-primary:hover { background: #cf1119; }
      .hai-secondary, .hai-chip, .hai-detail { border-radius: 0; }
      .hai-dropzone {
        min-height: 132px; margin: 13px; border: 1px dashed #f15d64; border-radius: 0;
        background: linear-gradient(180deg, #fff 0%, #fff9f0 100%); color: #333; gap: 10px;
        min-width: 0;
      }
      .hai-dropzone::after {
        content: ""; position: absolute; inset: 7px; border: 1px solid rgba(255, 202, 8, .55); pointer-events: none;
      }
      .hai-dropzone.hai-drag { border-color: #d51018; background: #fff3f0; }
      .hai-upload-icon {
        width: 48px; height: 48px; display: inline-flex; align-items: center; justify-content: center;
        background: #0b4ea2; color: #fff; border-radius: 50%;
      }
      .hai-upload-icon svg { width: 25px; height: 25px; }
      .hai-upload-copy { color: #555; }
      .hai-upload-copy span { overflow-wrap: anywhere; }
      .hai-upload-copy strong { color: #0b4ea2; font-size: 16px; }
      .hai-preview { padding: 0 13px 13px; }
      .hai-preview img { border-radius: 0; width: 104px; height: 104px; }
      .hai-error, .hai-loading, .hai-notice { border-radius: 0; font-size: 13px; }
      .hai-loading { border-color: #ffdb6f; background: #fff8d9; color: #684a00; font-weight: 700; }
      .hai-categories, .hai-top, .hai-items {
        border-top: 1px solid #ececec; padding-top: 16px; gap: 14px;
      }
      .hai-categories h3, .hai-top h3, .hai-items h3 {
        display: flex; align-items: center; gap: 8px; color: #111; font-size: 18px; font-weight: 900;
      }
      .hai-categories h3 span, .hai-top h3 span, .hai-items h3 span {
        display: inline-block; width: 4px; height: 22px; background: var(--hai-accent, #ee1b24);
      }
      .hai-category-list { gap: 7px; }
      .hai-chip {
        min-height: 36px; border-color: #d8d8d8; background: #fff; color: #333; padding: 0 14px;
      }
      .hai-chip:hover { border-color: var(--hai-accent, #ee1b24); color: var(--hai-accent, #ee1b24); }
      .hai-top-list, .hai-item-list { gap: 14px; }
      .hai-card {
        border: 1px solid #e0e0e0; border-radius: 0; box-shadow: none;
      }
      .hai-card:hover { border-color: #cfcfcf; box-shadow: 0 6px 16px rgba(0, 0, 0, .08); }
      .hai-image-link { position: relative; background: #f7f7f7; border-bottom: 1px solid #eee; }
      .hai-card img { object-fit: cover; }
      .hai-score {
        position: absolute; left: 10px; top: 10px; z-index: 1; display: inline-flex; align-items: center;
        min-height: 28px; padding: 0 9px; background: rgba(34, 34, 34, .9); color: #fff;
        font-size: 12px; font-weight: 900;
      }
      .hai-card-body { padding: 12px 13px 13px; gap: 7px; }
      .hai-product-id { color: #888; font-size: 12px; }
      .hai-card h4 { color: #111; min-height: 38px; font-size: 14px; font-weight: 800; }
      .hai-card-meta { min-height: 18px; color: #777; font-size: 12px; }
      .hai-price { color: #111; font-weight: 900; font-size: 17px; }
      .hai-detail {
        min-height: 34px; border-color: #e1e1e1; background: #fafafa; color: #333; font-size: 13px;
      }
      .hai-detail:hover { border-color: var(--hai-accent, #ee1b24); background: var(--hai-accent, #ee1b24); color: #fff; }
      .hai-more { border: 1px solid #d8d8d8; background: #fff; color: #333; }
      .hai-empty { border-radius: 0; }
      @media (max-width: 560px) {
        .hai-dialog { width: min(100vw - 16px, 680px); margin: 8px auto; max-height: calc(100vh - 16px); }
        .hai-header, .hai-body { padding-left: 14px; padding-right: 14px; }
        .hai-header { padding-left: 0; padding-right: 0; }
        .hai-brand-head { grid-template-columns: 1fr; padding: 22px 48px 14px 14px; gap: 8px; }
        .hai-brand-mark { display: none; }
        .hai-icon-button { position: fixed; top: 14px; right: 12px; z-index: 3; }
        .hai-header h2 { font-size: 19px; }
        .hai-badges { display: none; }
        .hai-mode-strip span { padding: 6px 5px; font-size: 12px; }
        .hai-upload-copy span { display: block; max-width: 250px; margin: 0 auto; }
        .hai-row, .hai-top-list, .hai-item-list { grid-template-columns: 1fr; }
      }
      @media (min-width: 561px) and (max-width: 860px) {
        .hai-dialog { width: min(100vw - 20px, 760px); margin: 10px auto; max-height: calc(100vh - 20px); }
        .hai-header, .hai-body { padding-left: 16px; padding-right: 16px; }
        .hai-header { padding-left: 0; padding-right: 0; }
        .hai-brand-head { padding-left: 16px; padding-right: 52px; }
        .hai-icon-button { position: fixed; top: 14px; right: 12px; z-index: 3; }
        .hai-mode-strip span { padding: 6px 5px; font-size: 12px; }
        .hai-row { grid-template-columns: 1fr; }
        .hai-top-list, .hai-item-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      }
      @media (min-width: 861px) and (max-width: 1120px) {
        .hai-item-list { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      }
    `;
    document.head.appendChild(style);
  }

  function trimSlash(value) {
    return String(value || "").replace(/\/$/, "");
  }

  function normalizeApiBaseUrl(value) {
    const text = trimText(value).replace(/\/+$/, "");
    if (isSafeApiBaseUrl(text)) {
      return text;
    }
    return "";
  }

  function normalizeResultPageUrl(value) {
    const text = trimText(value);
    if (!text || /[\u0000-\u001f\u007f\s\\]/.test(text) || text.charAt(0) === "#") {
      return "";
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(text)) {
      if (!/^https?:\/\//i.test(text)) {
        return "";
      }
      if (hasUnsafeUrlAuthority(text)) {
        return "";
      }
      return text;
    }
    if (/^\/\//.test(text)) {
      return "";
    }
    return text;
  }

  function normalizeResultPageTarget(value) {
    return trimText(value) === "_blank" ? "_blank" : "_self";
  }

  function inferApiBaseUrlFromScript() {
    const script = currentWidgetScriptElement();
    const source = script ? scriptSource(script) : "";
    return apiBaseUrlFromScriptSrc(source);
  }

  function currentWidgetScriptElement() {
    const current = document.currentScript;
    if (current) {
      const currentSource = scriptSource(current);
      if (isWidgetScriptSource(currentSource) || truthyAttribute(firstAttributeValue(current, ["data-hai-auto-init", "data-haeorum-auto-init", "data-auto-init"]))) {
        return current;
      }
    }
    const scripts = arrayFrom(document.querySelectorAll ? document.querySelectorAll("script") : []);
    for (let index = scripts.length - 1; index >= 0; index -= 1) {
      const script = scripts[index];
      if (isWidgetScriptSource(scriptSource(script))) {
        return script;
      }
    }
    return null;
  }

  function scriptSource(script) {
    return trimText(script && (script.src || (typeof script.getAttribute === "function" ? script.getAttribute("src") : "")));
  }

  function isWidgetScriptSource(value) {
    return /(^|\/)widget(?:\.min)?\.js(?:[?#]|$)/i.test(trimText(value));
  }

  function apiBaseUrlFromScriptSrc(value) {
    const text = trimText(value);
    if (!/^https?:\/\//i.test(text) || /[\u0000-\u001f\u007f\s\\]/.test(text) || hasUnsafeApiUrlAuthority(text)) {
      return "";
    }
    const match = text.match(/^([a-z][a-z0-9+.-]*:\/\/[^/?#]+)/i);
    return match ? normalizeApiBaseUrl(match[1]) : "";
  }

  function isSafeApiBaseUrl(value) {
    const text = String(value || "").trim();
    if (!text || /[\u0000-\u001f\u007f\s\\]/.test(text)) {
      return false;
    }
    if (!/^https?:\/\//i.test(text)) {
      return false;
    }
    if (/[?#]/.test(text)) {
      return false;
    }
    return !hasUnsafeApiUrlAuthority(text);
  }

  function hasUnsafeApiUrlAuthority(value) {
    const authority = String(value || "").replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[/?#]/)[0];
    if (!authority || authority.indexOf("@") !== -1) {
      return true;
    }
    return !extractAuthorityHost(authority);
  }

  function trimText(value) {
    return String(value || "").trim();
  }

  function normalizeMallIdentifier(value, optionName) {
    const text = trimText(value);
    if (!text) {
      return "";
    }
    if (!MALL_ID_PATTERN.test(text)) {
      throw new Error(optionName + " must contain only letters, numbers, and hyphens, starting and ending with a letter or number.");
    }
    return text;
  }

  function numberWithCommas(value) {
    return Math.round(Number(value)).toLocaleString("ko-KR");
  }

  function safeLinkUrl(value) {
    const text = trimText(value);
    if (!text) {
      return "#";
    }
    if (isSafeAbsoluteBrowserUrl(text)) {
      return text;
    }
    return "#";
  }

  function safeImageUrl(value) {
    const text = trimText(value);
    if (!text) {
      return "";
    }
    if (isSafeAbsoluteBrowserUrl(text)) {
      return text;
    }
    return "";
  }

  function isSafeAbsoluteBrowserUrl(value) {
    const text = String(value || "").trim();
    if (!text || /[\u0000-\u001f\u007f\s\\]/.test(text)) {
      return false;
    }
    if (!/^https?:\/\//i.test(text)) {
      return false;
    }
    return !hasUnsafeUrlAuthority(text);
  }

  function hasUnsafeUrlAuthority(value) {
    const authority = String(value || "").replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[/?#]/)[0];
    if (!authority || authority.indexOf("@") !== -1) {
      return true;
    }
    const host = extractAuthorityHost(authority);
    if (!host || isUnsafeLocalBrowserHost(host)) {
      return true;
    }
    return false;
  }

  function extractAuthorityHost(authority) {
    if (authority.charAt(0) === "[") {
      const closeIndex = authority.indexOf("]");
      if (closeIndex < 0) {
        return "";
      }
      const host = authority.slice(1, closeIndex);
      const portPart = authority.slice(closeIndex + 1);
      if (portPart && !isValidPortPart(portPart)) {
        return "";
      }
      return normalizeBrowserHost(host);
    }
    const colonIndex = authority.lastIndexOf(":");
    if (colonIndex >= 0) {
      const host = authority.slice(0, colonIndex);
      const portPart = authority.slice(colonIndex);
      if (!host || !isValidPortPart(portPart)) {
        return "";
      }
      return normalizeBrowserHost(host);
    }
    return normalizeBrowserHost(authority);
  }

  function isValidPortPart(value) {
    if (!/^:\d{1,5}$/.test(value)) {
      return false;
    }
    const port = Number(value.slice(1));
    return Number.isInteger(port) && port >= 1 && port <= 65535;
  }

  function normalizeBrowserHost(value) {
    return String(value || "").trim().toLowerCase().replace(/\.$/, "");
  }

  function isUnsafeLocalBrowserHost(value) {
    const host = normalizeBrowserHost(value);
    if (!host) {
      return true;
    }
    if (host === "localhost" || host === "0.0.0.0" || host === "::" || host === "::1" || host.endsWith(".localhost")) {
      return true;
    }
    const ipv4 = parseIpv4Host(host);
    if (ipv4) {
      return ipv4[0] === 127 || (ipv4[0] === 169 && ipv4[1] === 254) || ipv4.every(function (part) { return part === 0; });
    }
    const firstIpv6Part = host.indexOf(":") >= 0 ? host.split(":")[0] : "";
    return /^fe[89ab][0-9a-f]$/i.test(firstIpv6Part);
  }

  function parseIpv4Host(host) {
    const parts = String(host || "").split(".");
    if (parts.length !== 4) {
      return null;
    }
    const numbers = [];
    for (let index = 0; index < parts.length; index += 1) {
      if (!/^\d{1,3}$/.test(parts[index])) {
        return null;
      }
      const number = Number(parts[index]);
      if (!Number.isInteger(number) || number < 0 || number > 255) {
        return null;
      }
      numbers.push(number);
    }
    return numbers;
  }

  function autoInitFromScript() {
    const script = currentWidgetScriptElement();
    if (!script || !truthyAttribute(firstAttributeValue(script, ["data-hai-auto-init", "data-haeorum-auto-init", "data-auto-init"]))) {
      return;
    }
    init(optionsFromScriptAttributes(script));
  }

  function optionsFromScriptAttributes(script) {
    const options = {};
    [
      ["target", ["data-hai-target", "data-target"]],
      ["mallId", ["data-hai-mall-id", "data-mall-id"]],
      ["siteId", ["data-hai-site-id", "data-site-id"]],
      ["apiBaseUrl", ["data-hai-api-base-url", "data-api-base-url"]],
      ["apiKey", ["data-hai-api-key", "data-api-key"]],
      ["resultPageUrl", ["data-hai-result-page-url", "data-result-page-url"]],
      ["resultPageTarget", ["data-hai-result-page-target", "data-result-page-target"]],
      ["attachToSearchInput", ["data-hai-attach-to-search-input", "data-attach-to-search-input"]],
      ["attachAfterSelector", ["data-hai-attach-after-selector", "data-attach-after-selector"]],
      ["triggerTitle", ["data-hai-trigger-title", "data-trigger-title"]],
      ["triggerAriaLabel", ["data-hai-trigger-aria-label", "data-trigger-aria-label"]],
      ["accentColor", ["data-hai-accent-color", "data-accent-color"]],
      ["accentTextColor", ["data-hai-accent-text-color", "data-accent-text-color"]],
      ["accentSoftColor", ["data-hai-accent-soft-color", "data-accent-soft-color"]]
    ].forEach(function (entry) {
      const value = firstAttributeValue(script, entry[1]);
      if (value !== null) {
        options[entry[0]] = value;
      }
    });
    [
      ["limit", ["data-hai-limit", "data-limit"]],
      ["maxImageMb", ["data-hai-max-image-mb", "data-max-image-mb"]],
      ["minImageDimension", ["data-hai-min-image-dimension", "data-min-image-dimension"]],
      ["mountWaitMs", ["data-hai-mount-wait-ms", "data-mount-wait-ms"]],
      ["zIndex", ["data-hai-z-index", "data-z-index"]]
    ].forEach(function (entry) {
      const value = numberAttribute(firstAttributeValue(script, entry[1]));
      if (value !== null) {
        options[entry[0]] = value;
      }
    });
    [
      ["autoAttach", ["data-hai-auto-attach", "data-auto-attach"]],
      ["fallbackFloating", ["data-hai-fallback-floating", "data-fallback-floating"]],
      ["prefillFromSearchInput", ["data-hai-prefill-from-search-input", "data-prefill-from-search-input"]]
    ].forEach(function (entry) {
      const value = booleanAttribute(firstAttributeValue(script, entry[1]));
      if (value !== null) {
        options[entry[0]] = value;
      }
    });
    return options;
  }

  function firstAttributeValue(element, names) {
    if (!element || typeof element.getAttribute !== "function") {
      return null;
    }
    for (let index = 0; index < names.length; index += 1) {
      const value = element.getAttribute(names[index]);
      if (value !== null && value !== undefined) {
        return trimText(value);
      }
    }
    return null;
  }

  function truthyAttribute(value) {
    if (value === null || value === undefined) {
      return false;
    }
    const text = trimText(value).toLowerCase();
    return !text || ["1", "true", "yes", "y", "on"].indexOf(text) !== -1;
  }

  function booleanAttribute(value) {
    if (value === null || value === undefined) {
      return null;
    }
    const text = trimText(value).toLowerCase();
    if (["1", "true", "yes", "y", "on"].indexOf(text) !== -1) {
      return true;
    }
    if (["0", "false", "no", "n", "off"].indexOf(text) !== -1) {
      return false;
    }
    return null;
  }

  function numberAttribute(value) {
    if (value === null || value === undefined || trimText(value) === "") {
      return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatScore(product) {
    if (product.score_percent !== null && product.score_percent !== undefined) {
      return "유사도 " + formatPercent(product.score_percent) + "%";
    }
    if (product.score !== null && product.score !== undefined) {
      return "유사도 " + formatPercent(Number(product.score) * 100) + "%";
    }
    return "유사도 -";
  }

  function formatPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "-";
    }
    return number.toFixed(1).replace(/\.0$/, "");
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[char];
    });
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function aiSearchIcon() {
    return '<svg class="hai-camera-icon hai-ai-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z"/><circle cx="12" cy="13" r="3"/></svg>';
  }

  function xIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  }

  window.HaeorumAISearch = { init: init, open: open, close: close, destroy: destroy };
  autoInitFromScript();
})();
