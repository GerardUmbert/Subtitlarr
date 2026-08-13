// Shared across every docs/*.html page: mobile nav toggle + GSAP
// scroll-reveal entrance animations. Loaded after gsap.min.js and
// ScrollTrigger.min.js on each page.
(function () {
  var toggle = document.getElementById('nav-toggle');
  var nav = document.getElementById('site-nav');
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var isOpen = nav.classList.toggle('nav-open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
    nav.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () {
        nav.classList.remove('nav-open');
        toggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  if (typeof gsap === 'undefined') return; // CDN blocked/failed — leave content plainly visible

  document.documentElement.classList.add('js-motion');

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) {
    document.documentElement.classList.remove('js-motion');
    return;
  }

  gsap.registerPlugin(ScrollTrigger);

  gsap.utils.toArray('.reveal').forEach(function (el, i) {
    gsap.to(el, {
      opacity: 1,
      y: 0,
      duration: 0.6,
      ease: 'power3.out',
      delay: Math.min(i * 0.06, 0.3),
    });
  });

  gsap.utils.toArray('.reveal-scroll').forEach(function (el) {
    gsap.to(el, {
      opacity: 1,
      y: 0,
      duration: 0.5,
      ease: 'power3.out',
      scrollTrigger: {
        trigger: el,
        start: 'top 92%',
        toggleActions: 'play none none none',
      },
    });
  });

  // Images loading in after ScrollTrigger's initial position calculation
  // shift every trigger point below them — recalculating once each image
  // finishes (and once more after full load, as a safety net) keeps
  // "already in viewport" elements from getting stuck hidden because
  // their measured position was wrong at calculation time.
  document.querySelectorAll('img').forEach(function (img) {
    if (img.complete) return;
    img.addEventListener('load', function () { ScrollTrigger.refresh(); }, { once: true });
  });
  window.addEventListener('load', function () { ScrollTrigger.refresh(); });

  // Force an immediate check right after setup too, so anything already
  // in view animates in on load instead of waiting for the user to
  // scroll at all (which may never happen on a short page).
  ScrollTrigger.refresh();
})();
