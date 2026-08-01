// InboxZen — Client-side interactions

document.addEventListener('DOMContentLoaded', function() {
    var emailList = document.getElementById('email-list');
    var searchInput = document.getElementById('search-input');
    var unreadSwitch = document.getElementById('unreadSwitch');

    // ----------------------------------------
    // Email card click — select + load detail
    // ----------------------------------------
    window.selectEmail = function(emailId) {
        var cards = emailList.querySelectorAll('.email-card');
        cards.forEach(function(c) { c.classList.remove('selected'); });

        var card = emailList.querySelector('[data-email-id="' + emailId + '"]');
        if (card) {
            card.classList.add('selected');
        }
    };

    // ----------------------------------------
    // Segmented control — tab switching
    // ----------------------------------------
    document.querySelectorAll('.seg-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.seg-btn').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            var tab = btn.getAttribute('data-tab');
            var url = '/?tab=' + tab;
            var accountId = new URLSearchParams(window.location.search).get('account_id');
            if (accountId) url += '&account_id=' + accountId;
            window.location.href = url;
        });
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
                var tab = new URLSearchParams(window.location.search).get('tab') || 'important';
                var accountId = new URLSearchParams(window.location.search).get('account_id');
                var url = '/?tab=' + tab;
                if (accountId) url += '&account_id=' + accountId;
                if (query.length > 0) {
                    url += '&q=' + encodeURIComponent(query);
                }
                window.location.href = url;
            }, 450);
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
