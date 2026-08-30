(function () {
  'use strict';

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
  function mix(a, b, t) { return a + (b - a) * t; }
  function hexPart(value) { return ('0' + clamp(Math.round(value), 0, 255).toString(16)).slice(-2); }
  function rgbToHex(color) { return '#' + hexPart(color.r) + hexPart(color.g) + hexPart(color.b); }
  var raf = window.requestAnimationFrame || function (callback) { return setTimeout(function () { callback(nowMs()); }, 16); };
  var caf = window.cancelAnimationFrame || window.clearTimeout;
  function nowMs() { return window.performance && performance.now ? performance.now() : new Date().getTime(); }
  function stub() {
    return {
      start: function () {},
      stop: function () {},
      resize: function () {},
      setState: function () {},
      startAction: function () {},
      completeAction: function () {},
      setColor: function () {},
      failAction: function () {},
      setOffline: function () {},
      colorFromTool: colorFromTool,
      parseColor: parseColor
    };
  }

  var NAMED_COLORS = {
    red: '#ff3d24', orange: '#ff8c24', yellow: '#ffd34d', green: '#5dff9d',
    cyan: '#59f2ff', blue: '#4d8dff', purple: '#a864ff', pink: '#ff5faf',
    white: '#fff2d5'
  };
  var EFFECT_COLORS = {
    candlelight: '#481c07', date_night: '#5c227c', reading: '#be9a5c',
    ember: '#601604', moonlight: '#2a4e96', ocean: '#1c8a9a',
    aurora: '#3ac68e', stormwatch: '#2e425e', sunrise: '#da7426',
    lagoon: '#167c74', nebula: '#5644aa', fireplace: '#b24812',
    soft_rain: '#2a465c'
  };

  function parseColor(value) {
    var match, parts, div, computed;
    if (!value) return null;
    value = String(value).trim().toLowerCase();
    if (NAMED_COLORS[value]) value = NAMED_COLORS[value];
    match = value.match(/^#?([0-9a-f]{6})$/i);
    if (match) {
      return {
        r: parseInt(match[1].slice(0, 2), 16),
        g: parseInt(match[1].slice(2, 4), 16),
        b: parseInt(match[1].slice(4, 6), 16)
      };
    }
    match = value.match(/^rgb\(([^)]+)\)$/);
    if (match) {
      parts = match[1].split(',').map(function (part) { return Number(part.trim()); });
      if (parts.length >= 3) return { r: parts[0], g: parts[1], b: parts[2] };
    }
    match = value.match(/^hsl\(([^)]+)\)$/);
    if (match) {
      div = document.createElement('div');
      div.style.color = value;
      document.body.appendChild(div);
      computed = getComputedStyle(div).color;
      document.body.removeChild(div);
      return parseColor(computed);
    }
    return null;
  }

  function colorFromTool(name, args) {
    if (!args) return null;
    if (name === 'lotus_set_color') return parseColor(args.color);
    if (name === 'lotus_set_rgb') return { r: args.red, g: args.green, b: args.blue };
    if (name === 'lotus_set_effect') return parseColor(EFFECT_COLORS[args.effect]);
    if (name === 'lotus_sequence_effects' && args.effects && args.effects.length) return parseColor(EFFECT_COLORS[args.effects[0]]);
    if (name === 'lotus_turn_on') return parseColor('white');
    if (name === 'lotus_turn_off') return { r: 0, g: 0, b: 0 };
    if (args.color) return parseColor(args.color);
    if (args.hex) return parseColor(args.hex);
    if (args.red !== undefined && args.green !== undefined && args.blue !== undefined) {
      return { r: args.red, g: args.green, b: args.blue };
    }
    return null;
  }

  function create(canvas, readout) {
    var ctx, shell = document.getElementById('shell');
    if (!canvas || !canvas.getContext) return stub();
    ctx = canvas.getContext('2d');
    if (!ctx) return stub();
    var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var ua = navigator.userAgent || '';
    var lowPower = /(Windows NT 6\.[23]|Windows Phone|IEMobile|Trident|ARM;|Touch)/i.test(ua);
    var state = 'idle', actionLabel = '', running = false, frameId = 0, started = nowMs();
    var targetCore = { r: 255, g: 190, b: 70 }, core = { r: 255, g: 190, b: 70 };
    var action = null, particles = [], arcs = [], i, lastDraw = 0;

    for (i = 0; i < (lowPower ? 86 : 220); i += 1) {
      particles.push({
        a: Math.random() * Math.PI * 2,
        r: 0.18 + Math.random() * 0.72,
        z: Math.random(),
        s: 0.18 + Math.random() * 0.95,
        w: Math.random() * 2 - 1
      });
    }
    for (i = 0; i < (lowPower ? 16 : 46); i += 1) {
      arcs.push({
        a: Math.random() * Math.PI * 2,
        len: 0.14 + Math.random() * 0.38,
        r: 0.3 + Math.random() * 0.6,
        speed: 0.12 + Math.random() * 0.5
      });
    }

    function resize() {
      var rect = canvas.getBoundingClientRect(), scale = lowPower ? 1 : window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * scale));
      canvas.height = Math.max(1, Math.floor(rect.height * scale));
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
    }

    function stateEnergy() {
      if (state === 'offline') return 0.18;
      if (state === 'error') return 0.48;
      if (state === 'listening') return 0.72;
      if (state === 'thinking') return 0.88;
      if (state === 'speaking') return 0.78 + Math.sin(nowMs() / 120) * 0.14;
      if (state === 'executing') return 0.82;
      return 0.42;
    }

    function stateColor() {
      if (state === 'offline') return { r: 92, g: 72, b: 44 };
      if (state === 'error') return { r: 255, g: 84, b: 42 };
      if (state === 'listening') return { r: 255, g: 238, b: 196 };
      if (state === 'speaking') return { r: 255, g: 246, b: 210 };
      return { r: 255, g: 190, b: 70 };
    }

    function setReadout(nextState, message) {
      if (!readout) return;
      if (readout.status) readout.status.textContent = nextState === 'idle' ? 'JARVIS' : nextState.toUpperCase();
      if (readout.message && message) readout.message.textContent = message;
    }

    function setState(nextState, data) {
      state = nextState || 'idle';
      actionLabel = state === 'executing' && data && data.message ? data.message : '';
      targetCore = stateColor();
      setReadout(state, data && data.message);
    }

    function startAction(label, color) {
      action = color ? {
        color: color,
        progress: 0.04,
        target: 0.72,
        status: 'pending',
        started: nowMs()
      } : null;
      setState('executing', { message: label || 'Running action' });
    }

    function completeAction(color) {
      if (!action && color) startAction('Color changed', color);
      if (action) {
        if (color) action.color = color;
        action.status = 'success';
        action.target = 1;
      }
      setState('success', { message: 'Done.' });
      setTimeout(function () { if (state === 'success') setState('idle'); }, 900);
    }

    function setColor(color) {
      if (!color) return;
      targetCore = color;
      core = {
        r: mix(core.r, color.r, 0.12),
        g: mix(core.g, color.g, 0.12),
        b: mix(core.b, color.b, 0.12)
      };
    }

    function failAction(message) {
      if (action) {
        action.status = 'error';
        action.target = 0;
      }
      setState('error', { message: message || 'Connection issue.' });
    }

    function drawRing(cx, cy, radius, tilt, rotation, color, alpha, lineWidth, segments) {
      var step = Math.PI * 2 / segments, index, a, x, y, next, nx, ny;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(rotation);
      ctx.strokeStyle = color;
      ctx.globalAlpha = alpha;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      for (index = 0; index <= segments; index += 1) {
        a = index * step;
        next = a + step * 0.65;
        x = Math.cos(a) * radius;
        y = Math.sin(a) * radius * tilt;
        nx = Math.cos(next) * radius;
        ny = Math.sin(next) * radius * tilt;
        if (index % 4 === 0) ctx.moveTo(x, y);
        else ctx.lineTo(nx, ny);
      }
      ctx.stroke();
      ctx.restore();
    }

    function draw(now) {
      var rect = canvas.getBoundingClientRect(), w = rect.width, h = rect.height;
      var cx = w / 2, cy = h / 2, radius = Math.min(w, h) * 0.36, t = (now - started) / 1000;
      var energy = stateEnergy(), base = stateColor(), p, x, y, depth, glow, color, spread, ringColor, lineColor;
      if (!running) return;
      if (lowPower && now - lastDraw < 40) {
        frameId = raf(draw);
        return;
      }
      lastDraw = now;
      ctx.clearRect(0, 0, w, h);
      core = {
        r: mix(core.r, targetCore.r, 0.035),
        g: mix(core.g, targetCore.g, 0.035),
        b: mix(core.b, targetCore.b, 0.035)
      };
      if (action) {
        action.progress = mix(action.progress, action.target, action.status === 'success' ? 0.08 : 0.025);
        if (action.status === 'error' && action.progress < 0.04) action = null;
      }
      spread = action ? action.progress : 0;
      color = action ? action.color : core;
      ringColor = 'rgba(' + Math.round(mix(core.r, color.r, spread * 0.7)) + ',' + Math.round(mix(core.g, color.g, spread * 0.7)) + ',' + Math.round(mix(core.b, color.b, spread * 0.7)) + ',';
      lineColor = rgbToHex({ r: mix(core.r, color.r, spread), g: mix(core.g, color.g, spread), b: mix(core.b, color.b, spread) });

      glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 1.8);
      glow.addColorStop(0, ringColor + '0.7)');
      glow.addColorStop(0.22, ringColor + '0.28)');
      glow.addColorStop(0.74, 'rgba(0,0,0,0)');
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, w, h);

      drawRing(cx, cy, radius * 1.02, 0.34, t * 0.13, ringColor + '0.34)', 1, 1.2, lowPower ? 44 : 96);
      drawRing(cx, cy, radius * 0.86, 0.58, -t * 0.21, ringColor + '0.27)', 1, 1, lowPower ? 42 : 88);
      if (!lowPower) drawRing(cx, cy, radius * 1.14, 0.74, t * 0.08 + 0.9, ringColor + '0.18)', 1, 1, 80);

      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      for (i = 0; i < arcs.length; i += 1) {
        p = arcs[i];
        drawRing(cx, cy, radius * p.r, 0.78 - p.r * 0.25, t * p.speed + p.a, ringColor + (0.13 + energy * 0.2) + ')', 1, 0.7 + energy, lowPower ? 10 : 18);
      }
      for (i = 0; i < particles.length; i += 1) {
        p = particles[i];
        depth = 0.55 + Math.sin(t * p.s + p.z * 8) * 0.28;
        x = cx + Math.cos(p.a + t * (0.12 + energy * 0.18) * p.w) * radius * p.r * (0.8 + depth * 0.24);
        y = cy + Math.sin(p.a * 1.17 + t * (0.09 + energy * 0.12)) * radius * p.r * (0.44 + depth * 0.24);
        if (spread && (i / particles.length) < spread) ctx.fillStyle = 'rgba(' + color.r + ',' + color.g + ',' + color.b + ',' + (0.22 + energy * 0.45) + ')';
        else ctx.fillStyle = ringColor + (0.18 + energy * 0.38) + ')';
        ctx.beginPath();
        ctx.arc(x, y, (0.7 + p.z * 1.7) * (reduced ? 0.8 : 1), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.strokeStyle = lineColor;
      ctx.globalAlpha = 0.12 + energy * 0.18;
      ctx.lineWidth = 0.7;
      for (i = 0; i < (lowPower ? 18 : 54); i += 1) {
        p = particles[(i * 7) % particles.length];
        x = cx + Math.cos(p.a + t * 0.09) * radius * p.r;
        y = cy + Math.sin(p.a * 1.23 - t * 0.08) * radius * p.r * 0.58;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(t + i) * radius * 0.12, cy + Math.sin(t * 0.7 + i) * radius * 0.08);
        ctx.lineTo(x, y);
        ctx.stroke();
      }
      ctx.restore();

      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = 'rgba(' + Math.round(mix(base.r, color.r, spread)) + ',' + Math.round(mix(base.g, color.g, spread)) + ',' + Math.round(mix(base.b, color.b, spread)) + ',0.92)';
      ctx.shadowColor = ctx.fillStyle;
      ctx.shadowBlur = 34 + energy * 26;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * (0.08 + energy * 0.035 + Math.sin(t * 2.1) * 0.01), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      if (actionLabel && !lowPower) {
        ctx.fillStyle = 'rgba(255,220,150,0.72)';
        ctx.font = '12px Segoe UI, Arial, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(actionLabel, cx, cy + radius * 0.72);
      }

      frameId = raf(draw);
    }

    function start() {
      if (running) return;
      running = true;
      resize();
      frameId = raf(draw);
    }

    function stop() {
      running = false;
      if (frameId) caf(frameId);
      frameId = 0;
    }

    window.addEventListener('resize', resize);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop();
      else if (!shell || shell.getAttribute('data-interface') !== 'classic') start();
    });

    return {
      start: start,
      stop: stop,
      resize: resize,
      setState: setState,
      startAction: startAction,
      completeAction: completeAction,
      setColor: setColor,
      failAction: failAction,
      setOffline: function () { setState('offline', { message: 'Disconnected' }); },
      colorFromTool: colorFromTool,
      parseColor: parseColor
    };
  }

  window.JarvisSphere = { create: create, parseColor: parseColor, colorFromTool: colorFromTool };
}());
