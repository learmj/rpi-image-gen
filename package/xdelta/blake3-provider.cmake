# xdelta3 always fetches BLAKE3 with FetchContent. Satisfy that from the
# staged blake3 package instead, providing the plain blake3 target it links.
macro(ig_provide_blake3 method dep_name)
  if("${dep_name}" STREQUAL "blake3")
    find_package(blake3 CONFIG REQUIRED)
    add_library(blake3 ALIAS BLAKE3::blake3)
    FetchContent_SetPopulated(blake3)
  endif()
endmacro()

cmake_language(SET_DEPENDENCY_PROVIDER ig_provide_blake3
  SUPPORTED_METHODS FETCHCONTENT_MAKEAVAILABLE_SERIAL)
