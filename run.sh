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
    echo "Commands:"
    echo "  start       Start the application (default)"
    echo "  stop        Stop the application"
    echo "  restart     Restart the application"
    echo "  logs        Show application logs"
    echo "  status      Show container status"
    echo "  build       Rebuild and start"
    echo "  clean       Stop and remove all containers/volumes"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./run.sh              # Start the app"
    echo "  ./run.sh logs         # View logs"
    echo "  ./run.sh restart      # Restart after changes"
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
