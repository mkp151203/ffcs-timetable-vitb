from flask import Blueprint, render_template, Response, url_for
from datetime import datetime, timezone

sitemap_bp = Blueprint('sitemap', __name__)

@sitemap_bp.route('/sitemap.xml')
def sitemap_xml():
    """Generates an XML sitemap for search engines."""
    pages = []
    
    # Static pages
    # 'main.index' is the home page
    pages.append({
        'loc': url_for('main.index', _external=True),
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
    sitemap_xml_content = render_template('sitemap.xml', pages=pages)
    return Response(sitemap_xml_content.strip(), mimetype='application/xml')

@sitemap_bp.route('/sitemap')
def sitemap_html():
    """Generates an HTML sitemap for users."""
    links = []
    
    links.append({'title': 'Home', 'url': url_for('main.index')})
    links.append({'title': 'Login', 'url': url_for('auth.login')})
    # Add more if needed, e.g. from dynamic content if applicable later
    
    return render_template('sitemap.html', links=links)

@sitemap_bp.route('/robots.txt')
def robots_txt():
    """Generates robots.txt."""
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {url_for('sitemap.sitemap_xml', _external=True)}"
    ]
    return Response('\n'.join(lines), mimetype='text/plain')
