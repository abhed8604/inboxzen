// InboxZen JavaScript utilities
// Theme and WebSocket logic lives in base.html inline scripts
// This file is for any additional page-specific JS

document.addEventListener('DOMContentLoaded', function() {
    // HTMX after settle event — scroll to top of list for new emails
    document.body.addEventListener('htmx:oobAfterSwap', function(event) {
        if (event.detail.target.id === 'email-list') {
            event.detail.target.scrollTop = 0;
        }
    });
});