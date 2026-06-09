cmake_minimum_required(VERSION 3.17 FATAL_ERROR)

project(aes67-nmos-bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

find_package(nmos-cpp REQUIRED)

option(AES67_NMOS_BRIDGE_BUILD_TESTS "Build unit tests" ON)

# Core bridge logic: config, ownership marker, daemon REST adapter, reconciler.
# Depends on cpprestsdk (web::json / web::http) which is pulled in via nmos-cpp.
add_library(bridge_core STATIC
    src/bridge/config.cpp
    src/bridge/ownership.cpp
    src/bridge/daemon_client.cpp
    src/bridge/reconciler.cpp
)
target_include_directories(bridge_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(bridge_core PUBLIC
    nmos-cpp::compile-settings
    nmos-cpp::nmos-cpp
)

# The NMOS node application: builds the IS-04 resource graph and wires IS-05
# activation onto the daemon via nmos-cpp.
add_executable(aes67-nmos-bridge
    src/main.cpp
    src/nmos/nmos_resources.cpp
    src/nmos/connection.cpp
)
target_link_libraries(aes67-nmos-bridge PRIVATE bridge_core)

if(AES67_NMOS_BRIDGE_BUILD_TESTS)
    find_package(Catch2 REQUIRED)
    enable_testing()
    add_executable(bridge_tests
        tests/test_ownership.cpp
        tests/test_reconciler.cpp
        tests/test_config.cpp
    )
    target_link_libraries(bridge_tests PRIVATE bridge_core Catch2::Catch2WithMain)
    include(CTest)
    include(Catch)
    catch_discover_tests(bridge_tests)
endif()
