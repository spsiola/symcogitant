document.addEventListener('DOMContentLoaded', () => {
    const tabsNav = document.getElementById('tabs-nav');
    const tabsContentContainer = document.getElementById('tabs-content-container');
    const ws = new WebSocket(`ws://${window.location.host}/ws/logs`);
    
    // state mapping: source -> { btn, content, logContainer, metricsContainer, unreadCount }
    const tabs = {};
    let activeTabId = null;

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const source = data.source || 'harness core';
        
        ensureTabExists(source);
        
        if (data.type === 'metric') {
            updateMetrics(source, data);
        } else if (data.type === 'log') {
            appendLog(source, data);
        } else {
            // Render generic system events as raw JSON blocks
            appendRawEvent(source, data);
        }
        
        // update badge if not active
        if (activeTabId !== source) {
            tabs[source].unreadCount++;
            updateBadge(source);
        }
    };

    ws.onclose = () => {
        const source = 'harness core';
        ensureTabExists(source);
        appendLog(source, { level: 'WARNING', message: 'Connection lost. Please refresh.' });
    };

    function ensureTabExists(id) {
        if (tabs[id]) return;

        // Create Tab Button
        const btn = document.createElement('button');
        btn.className = 'tab-btn';
        btn.innerHTML = `<span>${id}</span> <span class="tab-badge" style="display:none">0</span>`;
        btn.onclick = () => switchTab(id);
        
        // Create Content Panel
        const content = document.createElement('div');
        content.className = 'tab-content';
        
        const metricsContainer = document.createElement('div');
        metricsContainer.className = 'metrics-container';
        metricsContainer.style.display = 'none'; // hidden by default until metrics arrive
        
        const logContainer = document.createElement('div');
        logContainer.className = 'log-container';
        
        content.appendChild(metricsContainer);
        content.appendChild(logContainer);
        
        tabsNav.appendChild(btn);
        tabsContentContainer.appendChild(content);
        
        tabs[id] = {
            btn: btn,
            content: content,
            logContainer: logContainer,
            metricsContainer: metricsContainer,
            unreadCount: 0
        };

        if (activeTabId === null) {
            switchTab(id);
        }
    }

    function switchTab(id) {
        if (activeTabId && tabs[activeTabId]) {
            tabs[activeTabId].btn.classList.remove('active');
            tabs[activeTabId].content.classList.remove('active');
        }
        
        activeTabId = id;
        tabs[id].btn.classList.add('active');
        tabs[id].content.classList.add('active');
        tabs[id].unreadCount = 0;
        updateBadge(id);
        
        // scroll to bottom
        tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
    }

    function updateBadge(id) {
        const badge = tabs[id].btn.querySelector('.tab-badge');
        if (tabs[id].unreadCount > 0) {
            badge.textContent = tabs[id].unreadCount > 99 ? '99+' : tabs[id].unreadCount;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }

    function appendLog(id, data) {
        const logContainer = tabs[id].logContainer;
        const entry = document.createElement('div');
        const levelClass = data.level ? data.level.toLowerCase() : 'info';
        entry.className = `log-entry ${levelClass}`;
        
        const time = new Date().toLocaleTimeString();
        const msg = data.message || '';
        
        entry.innerHTML = `
            <span style="color: #64748b">[${time}]</span>
            <span class="log-source">[${id}]</span>
            <span>${msg}</span>
        `;
        
        logContainer.appendChild(entry);
        
        if (activeTabId === id) {
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function appendRawEvent(id, data) {
        const logContainer = tabs[id].logContainer;
        const entry = document.createElement('div');
        entry.className = `log-entry debug`;
        
        const time = new Date().toLocaleTimeString();
        
        entry.innerHTML = `
            <span style="color: #64748b">[${time}]</span>
            <span class="log-source">[${id}]</span>
            <span style="color: #818cf8">[RAW EVENT]</span>
            <pre style="margin: 5px 0 5px 0; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 6px; font-family: monospace; white-space: pre-wrap; word-wrap: break-word; font-size: 0.85rem; color: #a5b4fc; border: 1px solid rgba(165, 180, 252, 0.2);">${JSON.stringify(data, null, 2)}</pre>
        `;
        
        logContainer.appendChild(entry);
        
        if (activeTabId === id) {
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function updateMetrics(id, payload) {
        const metricsContainer = tabs[id].metricsContainer;
        metricsContainer.style.display = 'flex'; // reveal metrics
        
        const data = payload.data;
        const timeStr = new Date(payload.timestamp * 1000).toLocaleTimeString();

        let html = '';
        
        if (data.cpu) {
            html += `
                <div class="metric-card">
                    <div class="metric-header">CPU Usage</div>
                    <div class="metric-body">${data.cpu.usage_percent}% (${data.cpu.cores} Cores)</div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        if (data.ram) {
            html += `
                <div class="metric-card">
                    <div class="metric-header">RAM Usage</div>
                    <div class="metric-body">${data.ram.usage_percent}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 5px;">
                        ${data.ram.used_gb} / ${data.ram.total_gb} GB
                    </div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        if (data.llms) {
            let llmsHtml = '';
            for (const [name, status] of Object.entries(data.llms)) {
                llmsHtml += `
                    <div class="llm-status">
                        <span>${name}</span>
                        <span class="status-lamp ${status}"></span>
                    </div>
                `;
            }
            
            html += `
                <div class="metric-card" style="flex: 2;">
                    <div class="metric-header">LLM Endpoints</div>
                    <div class="metric-body" style="font-weight: 400; font-family: 'Inter', sans-serif;">
                        ${llmsHtml}
                    </div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        metricsContainer.innerHTML = html;
        
        // Also log to history
        appendLog(id, {
            level: 'INFO',
            message: `[Metrics Updated] CPU: ${data.cpu ? data.cpu.usage_percent : '?'}%, RAM: ${data.ram ? data.ram.usage_percent : '?'}%`
        });
    }
});
