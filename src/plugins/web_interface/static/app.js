document.addEventListener('DOMContentLoaded', () => {
    const logContainer = document.getElementById('log-container');
    const ws = new WebSocket(`ws://${window.location.host}/ws/logs`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        appendLog(data);
    };

    ws.onclose = () => {
        appendLog({
            source: 'System',
            level: 'WARNING',
            message: 'Connection lost. Please refresh.'
        });
    };

    function appendLog(data) {
        const entry = document.createElement('div');
        const levelClass = data.level ? data.level.toLowerCase() : 'info';
        entry.className = `log-entry ${levelClass}`;
        
        const time = new Date().toLocaleTimeString();
        
        entry.innerHTML = `
            <span style="color: #64748b">[${time}]</span>
            <span class="log-source">[${data.source}]</span>
            <span>${data.message}</span>
        `;
        
        logContainer.appendChild(entry);
        
        // Auto scroll
        const main = logContainer.parentElement;
        main.scrollTop = main.scrollHeight;
    }
});
