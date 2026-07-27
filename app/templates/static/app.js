// InboxZen — Keyboard navigation, search, panel switching

document.addEventListener('DOMContentLoaded', function() {
    const emailList = document.getElementById('email-list');
    const searchInput = document.getElementById('search-input');
    const filterInput = document.getElementById('filter-input');
    let selectedIndex = -1;

    // ----------------------------------------
    // Keyboard shortcuts
    // ----------------------------------------
    document.addEventListener('keydown', function(e) {
        // Ctrl+K or Cmd+K — focus search
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            searchInput.focus();
            return;
        }

        // Escape — blur search/filter
        if (e.key === 'Escape') {
            document.activeElement.blur();
            return;
        }

        // Don't handle nav keys when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        const cards = emailList.querySelectorAll('.email-card');
        if (!cards.length) return;

        // j or ArrowDown — next email
        if (e.key === 'j' || e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIndex = Math.min(selectedIndex + 1, cards.length - 1);
            selectCard(cards, selectedIndex);
        }

        // k or ArrowUp — previous email
        if (e.key === 'k' || e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIndex = Math.max(selectedIndex - 1, 0);
            selectCard(cards, selectedIndex);
        }

        // Enter — open selected email
        if (e.key === 'Enter' && selectedIndex >= 0) {
            e.preventDefault();
            cards[selectedIndex].click();
        }
    });

    function selectCard(cards, index) {
        cards.forEach(function(c) { c.classList.remove('selected'); });
        cards[index].classList.add('selected');
        cards[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // ----------------------------------------
    // Email card click — select + load detail
    // ----------------------------------------
    window.selectEmail = function(emailId) {
        const cards = emailList.querySelectorAll('.email-card');
        cards.forEach(function(c) { c.classList.remove('selected'); });

        const card = emailList.querySelector('[data-email-id="' + emailId + '"]');
        if (card) {
            card.classList.add('selected');
            selectedIndex = Array.from(cards).indexOf(card);
        }
    };

    // ----------------------------------------
    // Search — live filtering
    // ----------------------------------------
    if (searchInput) {
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            const query = this.value.trim();
            searchTimeout = setTimeout(function() {
                if (query.length > 0) {
                    window.location.href = '/?q=' + encodeURIComponent(query);
                } else {
                    window.location.href = '/';
                }
            }, 400);
        });
    }

    // ----------------------------------------
    // Filter — live filtering
    // ----------------------------------------
    if (filterInput) {
        let filterTimeout;
        filterInput.addEventListener('input', function() {
            clearTimeout(filterTimeout);
            const query = this.value.trim().toLowerCase();
            filterTimeout = setTimeout(function() {
                const cards = emailList.querySelectorAll('.email-card');
                cards.forEach(function(card) {
                    const sender = card.querySelector('.card-sender');
                    const subject = card.querySelector('.card-subject');
                    const text = (sender ? sender.textContent : '') + ' ' + (subject ? subject.textContent : '');
                    card.style.display = text.toLowerCase().includes(query) ? '' : 'none';
                });
            }, 200);
        });
    }

    // ----------------------------------------
    // HTMX after swap — re-init selected state
    // ----------------------------------------
    document.body.addEventListener('htmx:afterSwap', function(event) {
        if (event.detail.target.id === 'detail-panel') {
            // Detail panel updated
        }
    });

    // ----------------------------------------
    // Highlight first card on load
    // ----------------------------------------
    const firstCard = emailList.querySelector('.email-card');
    if (firstCard) {
        selectedIndex = 0;
        firstCard.classList.add('selected');
    }
});
