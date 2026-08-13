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
    gsap.from(el, {
      opacity: 0,
      y: 16,
      duration: 0.5,
      ease: 'power3.out',
      scrollTrigger: { trigger: el, start: 'top 92%', toggleActions: 'play none none none' },
    });
  });
})();
