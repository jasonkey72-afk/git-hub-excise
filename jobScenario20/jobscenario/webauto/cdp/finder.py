"""
요소 탐색기(브라우저 안에서 실행되는 자바스크립트)
====================================================
selenium 판(locator.py)은 전략마다 파이썬 <-> 브라우저를 여러 번 왕복하지만,
CDP 판은 **한 번의 호출로 문서 한 개를 다 뒤지고 가장 알맞은 요소 하나를 돌려준다.**
왕복이 줄어 더 빠르고, 점수 계산 규칙이 이 함수 한 곳에 모인다.

전략 우선순위와 점수 규칙은 locator.py 와 같은 기준을 따른다.
(어느 프레임을 어떤 순서로 뒤질지는 파이썬 쪽 page.py 가 정한다)
"""

FIND_JS = r"""
function (spec) {
  function esc(v) {
    if (window.CSS && CSS.escape) { return CSS.escape(v); }
    return String(v).replace(/["\\\]]/g, '\\$&');
  }
  function qs(sel) {
    try { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
    catch (e) { return []; }
  }
  function xp(query) {
    var out = [];
    try {
      var it = document.evaluate(query, document, null,
                                 XPathResult.ORDERED_NODE_ITERATOR_TYPE, null), n;
      while ((n = it.iterateNext())) { out.push(n); }
    } catch (e) { return []; }
    return out;
  }
  // Shadow DOM 경계를 넘어가며 찾는다
  function deep(sel) {
    var out = [], seen = new Set();
    function walk(root) {
      if (!root || seen.has(root)) { return; }
      seen.add(root);
      try { root.querySelectorAll(sel).forEach(function (e) { out.push(e); }); }
      catch (e) { return; }
      var all = root.querySelectorAll('*');
      for (var i = 0; i < all.length; i++) {
        if (all[i].shadowRoot) { walk(all[i].shadowRoot); }
      }
    }
    walk(document);
    return out;
  }

  function byStrategy(s) {
    switch (s.by) {
      case 'id':           return qs('[id="' + esc(s.value) + '"]');
      case 'name':         return qs('[name="' + esc(s.value) + '"]');
      case 'css selector': return qs(s.value);
      case 'xpath':        return xp(s.value);
      case 'deep css':     return deep(s.value);
      case 'tag name':     return qs(s.value);
    }
    return [];
  }

  function visible(el) {
    var r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) { return false; }
    var st = window.getComputedStyle(el);
    if (!st || st.visibility === 'hidden' || st.display === 'none') { return false; }
    if (parseFloat(st.opacity || '1') < 0.05) { return false; }
    return true;
  }

  // locator.py 의 _score_element 와 같은 규칙
  function score(el) {
    if (!visible(el)) { return -1; }
    var s = 100;
    if (el.disabled) { s -= 40; }
    var r = el.getBoundingClientRect();
    s -= Math.min(Math.floor(Math.max(r.top, 0) / 200), 20);
    if (spec.tag && el.nodeName.toLowerCase() === String(spec.tag).toLowerCase()) { s += 15; }
    if (spec.text) {
      var t = (el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim();
      if (t === spec.text) { s += 25; }
      else if (t.indexOf(spec.text) >= 0) { s += 10; }
    }
    return s;
  }

  var strategies = spec.strategies || [];
  for (var i = 0; i < strategies.length; i++) {
    var found = byStrategy(strategies[i]);
    if (!found.length) { continue; }
    var best = null, bestScore = -1;
    for (var j = 0; j < found.length; j++) {
      var sc = score(found[j]);
      if (sc < 0 && spec.visibleOnly) { continue; }
      if (sc > bestScore) { bestScore = sc; best = found[j]; }
    }
    if (best) { return best; }
  }
  return null;
}
"""

# 요소 정보를 한 번에 읽어 오는 함수(왕복을 줄이기 위해 필요한 값을 모두 함께 돌려준다)
INFO_JS = r"""
function () {
  var r = this.getBoundingClientRect();
  return {
    tag: this.nodeName.toLowerCase(),
    text: (this.innerText || this.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200),
    value: this.value === undefined ? '' : String(this.value),
    enabled: !this.disabled,
    checked: !!this.checked,
    x: r.left + r.width / 2,
    y: r.top + r.height / 2,
    width: r.width,
    height: r.height
  };
}
"""

SCROLL_JS = """
function () {
  this.scrollIntoView({block: 'center', inline: 'center'});
  var r = this.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2,
          width: r.width, height: r.height};
}
"""

# 입력창 내용을 비우고 편집 위치를 잡는다
CLEAR_JS = """
function () {
  this.focus();
  if (this.select) { try { this.select(); } catch (e) {} }
  if (this.setSelectionRange && this.value !== undefined) {
    try { this.setSelectionRange(0, String(this.value).length); } catch (e) {}
  }
  return true;
}
"""

# 직접 입력이 막힌 입력창(달력 등)에 값을 강제로 넣는다
SET_VALUE_JS = """
function (v) {
  var proto = this instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  var d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) { d.set.call(this, v); } else { this.value = v; }
  this.dispatchEvent(new Event('input', {bubbles: true}));
  this.dispatchEvent(new Event('change', {bubbles: true}));
  return String(this.value);
}
"""

# 콤보박스에서 화면 글자 -> value -> 순번 순으로 선택한다
SELECT_JS = """
function (wanted) {
  if (this.nodeName.toLowerCase() !== 'select') { return 'not-select'; }
  var opts = this.options, i;
  for (i = 0; i < opts.length; i++) {
    if ((opts[i].text || '').trim() === wanted) { this.selectedIndex = i; break; }
  }
  if (i >= opts.length) {
    for (i = 0; i < opts.length; i++) {
      if (opts[i].value === wanted) { this.selectedIndex = i; break; }
    }
  }
  if (i >= opts.length) {
    var n = parseInt(wanted, 10);
    if (!isNaN(n) && n >= 0 && n < opts.length) { this.selectedIndex = n; i = n; }
    else { return 'no-option'; }
  }
  this.dispatchEvent(new Event('input', {bubbles: true}));
  this.dispatchEvent(new Event('change', {bubbles: true}));
  return 'ok';
}
"""

CLICK_JS = "function () { this.click(); return true; }"
