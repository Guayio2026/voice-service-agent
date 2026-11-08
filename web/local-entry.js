// file:// cannot run the authenticated same-origin microphone/backend flow.
if (location.protocol === 'file:') location.replace('http://127.0.0.1:8766/index.html');
