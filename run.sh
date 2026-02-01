#!/bin/bash
# LucidLink Labs: CONNECT Manager - macOS/Linux launcher

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

show_help() {
    echo "LucidLink Labs: CONNECT Manager"
    echo ""
    echo "Usage: ./run.sh [command]"
    echo ""
    echo "Development Commands:"
    echo "  start       Start the application (default)"
    echo "  stop        Stop the application"
    echo "  restart     Restart the application"
    echo "  logs        Show application logs"
    echo "  status      Show container status"
    echo "  build       Rebuild and start"
    echo "  clean       Stop and remove all containers/volumes"
    echo ""
    echo "Production Commands:"
    echo "  prod        Start with Caddy reverse proxy (HTTPS)"
    echo "  prod-build  Rebuild and start production"
    echo "  prod-stop   Stop production deployment"
    echo "  prod-logs   Show production logs"
    echo ""
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./run.sh              # Start dev mode (HTTP :8000)"
    echo "  ./run.sh prod         # Start prod mode (HTTPS :443)"
    echo "  ./run.sh logs         # View logs"
    echo ""
    echo "Production requires .env file with DOMAIN and JWT_SECRET_KEY"
    echo "See .env.example for details"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo "Error: Docker is not installed or not in PATH"
        echo "Please install Docker Desktop: https://www.docker.com/products/docker-desktop"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo "Error: Docker is not running"
        echo "Please start Docker Desktop and try again"
        exit 1
    fi
}

check_prod_env() {
    # Load .env file if it exists
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs)
    fi

    local missing=""

    if [ -z "$DOMAIN" ]; then
        missing="$missing DOMAIN"
    fi

    if [ -z "$JWT_SECRET_KEY" ]; then
        missing="$missing JWT_SECRET_KEY"
    fi

    if [ -n "$missing" ]; then
        echo "Error: Missing required environment variables for production:$missing"
        echo ""
        echo "Create a .env file with:"
        echo "  DOMAIN=your-domain.com"
        echo "  JWT_SECRET_KEY=\$(openssl rand -hex 32)"
        echo ""
        echo "See .env.example for details"
        exit 1
    fi
}

case "${1:-start}" in
    start)
        check_docker
        echo "Starting CONNECT Manager..."
        docker compose up -d
        echo ""
        echo "Application started at: http://localhost:8000"
        ;;
    stop)
        check_docker
        echo "Stopping CONNECT Manager..."
        docker compose down
        echo "Stopped."
        ;;
    restart)
        check_docker
        echo "Restarting CONNECT Manager..."
        docker compose down
        docker compose up -d
        echo ""
        echo "Application restarted at: http://localhost:8000"
        ;;
    logs)
        check_docker
        docker compose logs -f
        ;;
    status)
        check_docker
        docker compose ps
        ;;
    build)
        check_docker
        echo "Rebuilding and starting CONNECT Manager..."
        docker compose up --build -d
        echo ""
        echo "Application started at: http://localhost:8000"
        ;;
    clean)
        check_docker
        echo "Stopping and removing all containers and volumes..."
        docker compose down -v
        echo "Cleaned."
        ;;
    prod)
        check_docker
        check_prod_env
        echo "Starting CONNECT Manager in production mode..."
        docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        echo ""
        echo "Application started at: https://${DOMAIN}"
        ;;
    prod-build)
        check_docker
        check_prod_env
        echo "Rebuilding and starting CONNECT Manager in production mode..."
        docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
        echo ""
        echo "Application started at: https://${DOMAIN}"
        ;;
    prod-stop)
        check_docker
        echo "Stopping production CONNECT Manager..."
        docker compose -f docker-compose.yml -f docker-compose.prod.yml down
        echo "Stopped."
        ;;
    prod-logs)
        check_docker
        docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
