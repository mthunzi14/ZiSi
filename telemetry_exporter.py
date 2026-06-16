from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import sys

class TelemetryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/logs', '/']:
            try:
                # Run the pm2 logs command inside a shell
                result = subprocess.run(
                    'pm2 logs ZiSi-Core-Engine --lines 100 --no-color',
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                # Combine stdout and stderr outputs
                output = f"--- STDOUT ---\n{result.stdout}\n--- STDERR ---\n{result.stderr}"
                
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(output.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(f"Server Error: {str(e)}".encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

def run(port=9090):
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, TelemetryHandler)
    print(f"Starting telemetry exporter on port {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Stopping telemetry exporter...")

if __name__ == '__main__':
    port = 9090
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("Invalid port specified, using default: 9090")
    run(port)
