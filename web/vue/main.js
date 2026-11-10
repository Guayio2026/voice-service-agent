import { createApp, ref, onMounted } from '../vendor/vue/vue.esm-browser.prod.js';

// Page components own markup; existing media controllers run only after mount.
const pages = new Set(['index', 'workbench', 'report', 'about', 'guide', 'mock']);
const page = location.pathname.split('/').pop().replace('.html', '') || 'index';
async function start() {
  if (!pages.has(page)) throw Error('未知页面');
  const [{default: component}, {default: modules}] = await Promise.all([
    import(`./${page}.js?v=order-20261108d`), import(`./${page}-modules.js?v=order-20261108d`)
  ]);
  const query = new URLSearchParams(location.search);
  const initial = query.get('mode') === 'demo' || query.get('play') === '1' || ['#case-evidence', '#scenario'].includes(location.hash) ? 'demo' : 'voice';
  component.setup = () => {
    const mode = ref(initial);
    function selectMode(value) {
      mode.value = value;
      window.dispatchEvent(new CustomEvent('workspace-mode-change', {detail: value}));
      const url = new URL(location.href);
      url.searchParams.delete('play'); url.searchParams.set('mode', value);
      history.replaceState(null, '', url);
    }
    onMounted(async () => {
      try {
        for (const module of modules) await import(new URL('../' + module + '?v=order-20261108d', import.meta.url));
        document.getElementById('vue-app').dataset.ready = 'true';
      } catch (e) {
        const message = document.createElement('p');
        message.setAttribute('role', 'alert');
        message.textContent = '页面功能加载失败，请刷新重试：' + e.message;
        document.getElementById('vue-app').prepend(message);
      }
    });
    return {mode, selectMode};
  };
  createApp(component).mount('#vue-app');
}
start().catch(e => { document.getElementById('vue-app').textContent = '页面加载失败：' + e.message; });
