/**
 * i18n.js — Galleria Internationalisation Loader
 * ================================================
 * Lightweight, zero-dependency i18n module for the Galleria static frontend.
 *
 * How it works:
 *  1. On DOMContentLoaded, reads the saved language from localStorage
 *     (key: "galleria_lang").  Falls back to the browser's navigator.language,
 *     then to "en" if neither is in the supported list.
 *  2. Fetches translations/{lang}.json from the same origin (served by
 *     CloudFront / S3 with a 24-hour cache).
 *  3. Applies translations to every element that has a data-i18n attribute.
 *  4. Sets <html lang="…"> and dir="ltr"|"rtl" for accessibility and CSS.
 *
 * Attribute conventions used in HTML:
 *   data-i18n="key.path"           → sets element.textContent
 *   data-i18n-html="key.path"      → sets element.innerHTML (use sparingly)
 *   data-i18n-placeholder="key.path" → sets input.placeholder
 *   data-i18n-title="key.path"     → sets element.title (tooltip)
 *   data-i18n-aria="key.path"      → sets element.ariaLabel
 *
 * API exposed on window.i18n:
 *   t(key)               → translated string or key as fallback
 *   loadLanguage(code)   → load & apply a language; returns Promise<void>
 *   currentLang()        → active language code string
 *   applyTranslations()  → re-apply to DOM (call after dynamic HTML inserts)
 *
 * AWS Certification Note (SAA-C03 / DVA-C02 / CloudFront):
 *   Translation JSON files are served from CloudFront in front of S3.
 *   Cache-Control: max-age=86400 means a user fetches the file at most once
 *   per day.  CloudFront compression (gzip/br) reduces transfer size.
 *   Because the files are immutable per deployment, we could also use
 *   content-addressed filenames (e.g. en.abc123.json) with max-age=31536000,
 *   but for simplicity we use predictable names and a 24h cache.
 */

(function (global) {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  let _strings = {};          // active translation tree (nested object)
  let _lang = 'en';           // active language code
  let _rtl = false;           // is the active language right-to-left?
  let _manifest = null;       // languages.json array, loaded once

  // ── Key resolver ─────────────────────────────────────────────────────────
  /**
   * Resolve a dot-notation key against the loaded string tree.
   * Returns the translation string, or the key itself if not found
   * (so untranslated keys are still visible during development).
   *
   * @param {string} key  e.g. "auth.welcome_back"
   * @param {string} [fallback]  optional explicit fallback
   * @returns {string}
   */
  function t(key, fallback) {
    const val = key.split('.').reduce(function (obj, k) {
      return obj && typeof obj === 'object' ? obj[k] : undefined;
    }, _strings);
    if (val !== undefined && val !== null) return String(val);
    if (fallback !== undefined) return fallback;
    return key; // return the key so missing translations are visible
  }

  // ── DOM application ───────────────────────────────────────────────────────
  /**
   * Walk the DOM and apply translations to all annotated elements.
   * Safe to call multiple times (idempotent).
   */
  function applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n'));
      if (v) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-html'));
      if (v) el.innerHTML = v;
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-placeholder'));
      if (v) el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-title'));
      if (v) el.title = v;
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-aria'));
      if (v) el.setAttribute('aria-label', v);
    });

    // Update <html> lang + dir
    document.documentElement.lang = _lang;
    document.documentElement.dir = _rtl ? 'rtl' : 'ltr';

    // Fire a custom event so page JS can react (e.g. rebuild effects grid)
    document.dispatchEvent(new CustomEvent('i18n:ready', { detail: { lang: _lang } }));
  }

  // ── Language loader ───────────────────────────────────────────────────────
  /**
   * Load a language JSON file and apply it to the DOM.
   *
   * @param {string} langCode  BCP-47 code, e.g. "ja", "ar", "zh-TW"
   * @returns {Promise<void>}
   */
  function loadLanguage(langCode) {
    // English is always the fallback — we have it inline via the HTML
    var url = 'translations/' + langCode + '.json';

    return fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        _strings = data;
        _lang = langCode;
        _rtl = data._meta && data._meta.rtl ? true : false;
        localStorage.setItem('galleria_lang', langCode);
        applyTranslations();
      })
      .catch(function (err) {
        console.warn('[i18n] Could not load', url, err);
        // Fall back to English if not already
        if (langCode !== 'en') {
          _lang = 'en';
          _strings = {};
          _rtl = false;
          localStorage.setItem('galleria_lang', 'en');
          applyTranslations();
        }
      });
  }

  // ── Language manifest ─────────────────────────────────────────────────────
  /**
   * Fetch translations/languages.json and return the array.
   * Cached after first load.
   *
   * @returns {Promise<Array<{code:string, name:string, rtl:boolean}>>}
   */
  function getManifest() {
    if (_manifest) return Promise.resolve(_manifest);
    return fetch('translations/languages.json')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _manifest = data;
        return data;
      })
      .catch(function () {
        // Minimal fallback manifest so the switcher still renders
        _manifest = [{ code: 'en', name: 'English', rtl: false }];
        return _manifest;
      });
  }

  // ── Language switcher UI ──────────────────────────────────────────────────
  /**
   * Build and return a <select> element populated with all supported languages.
   * Inject this into your page wherever you want the switcher to appear.
   *
   * @param {string} [containerId]  optional id for a wrapper <div>
   * @returns {HTMLElement}  a <div> containing a globe icon + <select>
   */
  function buildSwitcher(containerId) {
    var wrap = document.createElement('div');
    wrap.className = 'lang-switcher';
    if (containerId) wrap.id = containerId;

    var icon = document.createElement('i');
    icon.className = 'ti ti-world';
    icon.setAttribute('aria-hidden', 'true');

    var sel = document.createElement('select');
    sel.className = 'lang-select';
    sel.setAttribute('aria-label', 'Select language');

    // Populate options after manifest loads
    getManifest().then(function (langs) {
      langs.forEach(function (l) {
        var opt = document.createElement('option');
        opt.value = l.code;
        opt.textContent = l.name;
        if (l.code === _lang) opt.selected = true;
        sel.appendChild(opt);
      });
    });

    sel.addEventListener('change', function () {
      loadLanguage(sel.value);
    });

    wrap.appendChild(icon);
    wrap.appendChild(sel);
    return wrap;
  }

  // ── Initialisation ─────────────────────────────────────────────────────────
  function init() {
    var stored = localStorage.getItem('galleria_lang');
    var browser = (navigator.language || 'en').split('-')[0];

    // Decide which language to load
    // Priority: stored preference > browser language > English
    var chosen = 'en';
    if (stored) {
      chosen = stored;
    } else if (browser && browser !== 'en') {
      // We'll try the browser language; loadLanguage falls back to 'en' on error
      chosen = browser;
    }

    // Always load the JSON so window.i18n.t() works for dynamically rendered
    // content (e.g. effects grid) regardless of language.
    loadLanguage(chosen);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  global.i18n = {
    t: t,
    loadLanguage: loadLanguage,
    applyTranslations: applyTranslations,
    currentLang: function () { return _lang; },
    buildSwitcher: buildSwitcher,
    getManifest: getManifest,
  };

  // Auto-init on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

}(window));
