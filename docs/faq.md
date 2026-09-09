# Frequently Asked Questions

[[_TOC_]]

## How can I fix the "Event loop is too busy" warning(s)?

These warnings are generated, if the [Async Event Loop](https://docs.python.org/3/library/asyncio-eventloop.html) is
blocked for too long by synchronous code. This typically happens when using blocking operations such as:

- `time.sleep()` instead of the non-blocking `await asyncio.sleep()`
- Using synchronous libraries instead of asynchronous ones, i.e. using `requests` instead of `aiohttp` for REST calls.
- CPU-intensive computations or other blocking synchronous functions

Blocking the event loop will cause connection timeouts to OPC UA clients and other problems.

### Best Practice:

Keep synchronous operations as short as possible, ideally under 5 milliseconds.
The easiest solution is to insert `await asyncio.sleep(0)` in synchronous loop bodies of async functions. If that is not
possible, or you need to run longer blocking operations (e.g., CPU-bound tasks or synchronous I/O), you can offload
them using:

- `asyncio.to_thread()` for I/O-bound
  tasks ([see here](https://docs.python.org/3/library/asyncio-task.html#running-in-threads))
- `loop.run_in_executor()` for CPU-bound
  tasks ([see here](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor))
