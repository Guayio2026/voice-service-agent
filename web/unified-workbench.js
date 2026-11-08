const voice = document.getElementById('voice-panel'), demo = document.getElementById('demo-panel');
const buttons = ['voice', 'demo'].map(mode => document.getElementById('mode-' + mode));
function select(mode) {
  voice.hidden = mode !== 'voice'; demo.hidden = mode !== 'demo';
  buttons.forEach((button, i) => button.setAttribute('aria-pressed', String((i === 0) === (mode === 'voice'))));
  window.dispatchEvent(new CustomEvent('workspace-mode-change', {detail: mode}));
  const url = new URL(location.href);
  url.searchParams.delete('play'); url.searchParams.set('mode', mode);
  history.replaceState(null, '', url);
}
buttons[0].onclick = () => select('voice');
buttons[1].onclick = () => select('demo');
const query = new URLSearchParams(location.search);
const initial = query.get('mode') === 'demo' || query.get('play') === '1' || ['#case-evidence', '#scenario'].includes(location.hash) ? 'demo' : 'voice';
// Preserve ?play=1 until the existing demo module handles its explicit autoplay link.
voice.hidden = initial !== 'voice'; demo.hidden = initial !== 'demo';
buttons.forEach((button, i) => button.setAttribute('aria-pressed', String((i === 0) === (initial === 'voice'))));
