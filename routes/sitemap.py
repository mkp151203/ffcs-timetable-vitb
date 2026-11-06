from flask import Blueprint, render_template, Response, url_for, current_app, send_from_directory
from datetime import datetime, timezone

sitemap_bp = Blueprint('sitemap', __name__)

@sitemap_bp.route('/sitemap.xml')
def sitemap_xml():
    """Generates an XML sitemap for search engines."""
    pages = []
    
    # Static pages
    # 'main.index' is the home page
    site_url = current_app.config['SITE_URL']
    pages.append({
        'loc': f"{site_url}/",
        'lastmod': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'changefreq': 'daily',
        'priority': '1.0'
    })
    
    # Auth pages - Removed per best practices (noindex for login)
    # pages.append({
    #     'loc': url_for('auth.login', _external=True),
    #     'lastmod': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
    #     'changefreq': 'monthly',
    #     'priority': '0.8'
    # })
    
    # Render XML
    # Render XML
    try:
        sitemap_xml_content = render_template('sitemap.xml', pages=pages)
        if not sitemap_xml_content:
            sitemap_xml_content = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    except Exception as e:
        print(f"Error rendering sitemap: {e}")
        sitemap_xml_content = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    
    return Response(sitemap_xml_content.strip(), mimetype='application/xml')



@sitemap_bp.route('/robots.txt')
def robots_txt():
    """Generates robots.txt. One wildcard group, so search and AI crawlers are treated alike."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /api/generate/page",
        "Disallow: /admin/",
        "Disallow: /auth/",
        "Disallow: /api/",
        "",
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml"
    ]
    return Response('\n'.join(lines), mimetype='text/plain')


@sitemap_bp.route('/favicon.ico')
def favicon():
    """Serve the favicon from the site root, where browsers and Google look for it."""
    return send_from_directory(current_app.static_folder, 'favicon.ico', mimetype='image/x-icon')


@sitemap_bp.route('/site.webmanifest')
def webmanifest():
    """Web app manifest (app name, icons, colours)."""
    return {
        'name': 'VIT Bhopal FFCS Timetable Maker',
        'short_name': 'FFCS VIT-B',
        'start_url': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#1e3a5f',
        'icons': [
            {'src': url_for('static', filename='icon-96.png'), 'sizes': '96x96', 'type': 'image/png'},
            {'src': url_for('static', filename='icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
        ],
    }, 200, {'Content-Type': 'application/manifest+json'}


@sitemap_bp.route('/llms.txt')
def llms_txt():
    """Plain-Markdown summary of the site for AI assistants (llmstxt.org)."""
    content = render_template('llms.txt', site_url=current_app.config['SITE_URL'])
    return Response(content, mimetype='text/markdown')
