// InboxZen — Client-side interactions

document.addEventListener('DOMContentLoaded', function() {
    var emailList = document.getElementById('email-list');
    var searchInput = document.getElementById('search-input');
    var unreadSwitch = document.getElementById('unreadSwitch');

    // ----------------------------------------
    // Email card click — select + load detail
    // ----------------------------------------
    window.selectEmail = function(emailId) {
        if (emailList) {
            var cards = emailList.querySelectorAll('.email-card');
            cards.forEach(function(c) { c.classList.remove('selected'); });

            var card = emailList.querySelector('[data-email-id="' + emailId + '"]');
            if (card) {
                card.classList.add('selected');
                card.classList.remove('is-unread');
                card.classList.add('read');
            }
        }
    };

    // ----------------------------------------
    // Fast tab switching & live search
    // ----------------------------------------
    window.refreshInboxList = function(targetTab, targetQuery) {
        var currentParams = new URLSearchParams(window.location.search);
        if (targetTab !== undefined) currentParams.set('tab', targetTab);
        if (targetQuery !== undefined) {
            if (targetQuery) currentParams.set('q', targetQuery);
            else currentParams.delete('q');
        }

        var listUrl = '/inbox/list?' + currentParams.toString();
        var pageUrl = '/?' + currentParams.toString();

        window.history.replaceState({}, '', pageUrl);

        fetch(listUrl)
            .then(function(res) {
                var impCount = res.headers.get('X-Important-Count');
                var totCount = res.headers.get('X-Total-Count');
                if (impCount !== null) {
                    var impEl = document.querySelector('[data-tab="important"] .seg-count');
                    if (impEl) impEl.textContent = impCount;
                }
                if (totCount !== null) {
                    var totEl = document.querySelector('[data-tab="all"] .seg-count');
                    if (totEl) totEl.textContent = totCount;
                }
                return res.text();
            })
            .then(function(html) {
                if (emailList) {
                    emailList.innerHTML = html;
                    if (window.htmx) {
                        htmx.process(emailList);
                    }
                    var firstCard = emailList.querySelector('.email-card');
                    if (firstCard) {
                        firstCard.click();
                    } else {
                        var detailPanel = document.getElementById('detail-panel');
                        if (detailPanel) {
                            detailPanel.innerHTML = '<div class="reading-scroll"><div class="empty-detail"><div class="empty-detail-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></div><p>No emails found</p></div></div>';
                        }
                    }
                }
            })
            .catch(function(err) {
                console.error("Failed to load email list", err);
            });
    };

    document.querySelectorAll('.seg-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.seg-btn').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            var tab = btn.getAttribute('data-tab');
            window.refreshInboxList(tab, undefined);
        });
    });

    window.addEventListener('popstate', function() {
        var params = new URLSearchParams(window.location.search);
        var tab = params.get('tab') || 'important';
        var q = params.get('q') || '';
        document.querySelectorAll('.seg-btn').forEach(function(b) {
            b.classList.toggle('active', b.getAttribute('data-tab') === tab);
        });
        if (searchInput) searchInput.value = q;
        window.refreshInboxList(tab, q);
    });

    // ----------------------------------------
    // Unread toggle — client-side filter
    // ----------------------------------------
    if (unreadSwitch) {
        unreadSwitch.addEventListener('change', function() {
            if (this.checked) {
                emailList.classList.add('show-unread-only');
            } else {
                emailList.classList.remove('show-unread-only');
            }
        });
    }

    // ----------------------------------------
    // Search — Ctrl+K / Cmd+K shortcut & input handler
    // ----------------------------------------
    document.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            if (searchInput) {
                e.preventDefault();
                searchInput.focus();
                searchInput.select();
            }
        }
    });

    if (searchInput) {
        var searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            var query = this.value.trim();
            searchTimeout = setTimeout(function() {
                window.refreshInboxList(undefined, query);
            }, 250);
        });
    }

    // ----------------------------------------
    // Highlight selected card on load
    // ----------------------------------------
    if (emailList) {
        var selectedCard = emailList.querySelector('.email-card.selected');
        if (!selectedCard) {
            var firstCard = emailList.querySelector('.email-card');
            if (firstCard) firstCard.classList.add('selected');
        }
    }

    // ----------------------------------------
    // Toast auto-dismiss
    // ----------------------------------------
    var toastContainer = document.getElementById('toast-container');
    if (toastContainer) {
        var toastObserver = new MutationObserver(function(mutations) {
            mutations.forEach(function(mutation) {
                mutation.addedNodes.forEach(function(node) {
                    if (node.nodeType === 1 && node.classList.contains('toast')) {
                        setTimeout(function() {
                            node.classList.add('removing');
                            setTimeout(function() { node.remove(); }, 250);
                        }, 3000);
                    }
                });
            });
        });
        toastObserver.observe(toastContainer, { childList: true });
    }
});
