@echo off
REM LucidLink Labs: CONNECT Manager - Windows launcher

setlocal EnableDelayedExpansion
cd /d "%~dp0"

if "%1"=="" goto start
if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="restart" goto restart
if "%1"=="logs" goto logs
if "%1"=="status" goto status
if "%1"=="build" goto build
if "%1"=="clean" goto clean
if "%1"=="help" goto help
if "%1"=="--help" goto help
if "%1"=="-h" goto help
goto unknown

:check_docker
where docker >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Docker is not installed or not in PATH
    echo Please install Docker Desktop: https://www.docker.com/products/docker-desktop
    exit /b 1
)
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Docker is not running
    echo Please start Docker Desktop and try again
    exit /b 1
)
exit /b 0

:start
call :check_docker
if %errorlevel% neq 0 exit /b 1
echo Starting CONNECT Manager...
docker compose up -d
echo.
echo Application started at: http://localhost:8000
goto end

:stop
call :check_docker
if %errorlevel% neq 0 exit /b 1
echo Stopping CONNECT Manager...
docker compose down
echo Stopped.
goto end

:restart
call :check_docker
if %errorlevel% neq 0 exit /b 1
echo Restarting CONNECT Manager...
docker compose down
docker compose up -d
echo.
echo Application restarted at: http://localhost:8000
goto end

:logs
call :check_docker
if %errorlevel% neq 0 exit /b 1
docker compose logs -f
goto end

:status
call :check_docker
if %errorlevel% neq 0 exit /b 1
docker compose ps
goto end

:build
call :check_docker
if %errorlevel% neq 0 exit /b 1
echo Rebuilding and starting CONNECT Manager...
docker compose up --build -d
echo.
echo Application started at: http://localhost:8000
goto end

:clean
call :check_docker
if %errorlevel% neq 0 exit /b 1
echo Stopping and removing all containers and volumes...
docker compose down -v
echo Cleaned.
goto end

:help
echo LucidLink Labs: CONNECT Manager
echo.
echo Usage: run.bat [command]
echo.
echo Commands:
echo   start       Start the application (default)
echo   stop        Stop the application
echo   restart     Restart the application
echo   logs        Show application logs
echo   status      Show container status
echo   build       Rebuild and start
echo   clean       Stop and remove all containers/volumes
echo   help        Show this help message
echo.
echo Examples:
echo   run.bat              Start the app
echo   run.bat logs         View logs
echo   run.bat restart      Restart after changes
goto end

:unknown
echo Unknown command: %1
echo.
goto help

:end
endlocal
