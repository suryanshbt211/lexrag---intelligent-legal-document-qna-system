import requests
import json
from typing import Dict, Optional
import time

class GeoLocationTracker:
    """Utility class for tracking user location based on IP address"""
    
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 3600  # 1 hour cache
    
    def get_location_from_ip(self, ip_address: str) -> Optional[Dict]:
        """
        Get location information from IP address using free geolocation API
        Returns: Dict with country, city, region, latitude, longitude
        """
        # Handle localhost and private IPs
        if ip_address in ['127.0.0.1', 'localhost', '::1'] or ip_address.startswith(('192.168.', '10.', '172.')):
            return {
                'country': 'Local Development',
                'city': 'Localhost',
                'region': 'Development',
                'latitude': 0.0,
                'longitude': 0.0
            }
        
        # Check cache first
        if ip_address in self.cache:
            cache_entry = self.cache[ip_address]
            if time.time() - cache_entry['timestamp'] < self.cache_ttl:
                return cache_entry['data']
        
        try:
            # Use ipapi.co for geolocation (free tier)
            response = requests.get(f'https://ipapi.co/{ip_address}/json/', timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                
                location_info = {
                    'country': data.get('country_name', 'Unknown'),
                    'city': data.get('city', 'Unknown'),
                    'region': data.get('region', 'Unknown'),
                    'latitude': float(data.get('latitude', 0)),
                    'longitude': float(data.get('longitude', 0))
                }
                
                # Cache the result
                self.cache[ip_address] = {
                    'data': location_info,
                    'timestamp': time.time()
                }
                
                return location_info
            else:
                print(f"Geolocation API returned status {response.status_code}")
                return self._get_fallback_location()
                
        except requests.exceptions.RequestException as e:
            print(f"Geolocation request failed: {str(e)}")
            return self._get_fallback_location()
        except Exception as e:
            print(f"Geolocation error: {str(e)}")
            return self._get_fallback_location()
    
    def _get_fallback_location(self) -> Dict:
        """Return fallback location data when geolocation fails"""
        return {
            'country': 'Unknown',
            'city': 'Unknown',
            'region': 'Unknown',
            'latitude': 0.0,
            'longitude': 0.0
        }
    
    def get_user_location_info(self, request) -> Dict:
        """Get comprehensive user location information from request"""
        # Get real IP address (handles proxies)
        if request.headers.get('X-Forwarded-For'):
            ip_address = request.headers.get('X-Forwarded-For').split(',')[0].strip()
        elif request.headers.get('X-Real-IP'):
            ip_address = request.headers.get('X-Real-IP')
        else:
            ip_address = request.remote_addr
        
        # Get location data
        location_data = self.get_location_from_ip(ip_address)
        
        return {
            'ip_address': ip_address,
            'user_agent': request.headers.get('User-Agent', 'Unknown'),
            'country': location_data['country'],
            'city': location_data['city'],
            'region': location_data['region'],
            'latitude': location_data['latitude'],
            'longitude': location_data['longitude']
        }
    
    def format_location_string(self, location_data: Dict) -> str:
        """Format location data into a readable string"""
        if not location_data:
            return "Unknown"
        
        parts = []
        if location_data.get('city'):
            parts.append(location_data['city'])
        if location_data.get('region'):
            parts.append(location_data['region'])
        if location_data.get('country'):
            parts.append(location_data['country'])
        
        return ", ".join(parts) if parts else "Unknown"
